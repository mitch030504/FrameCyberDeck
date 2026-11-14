"""Unity: disable Meta environment depth occlusion (EnvironmentDepthManager). From PR #246 (@alicecatpaw).

Steam Frame lacks Meta Quest's physical depth sensor (XR_META_environment_depth), leaving
environment depth textures uninitialized. In shaders using Meta's Depth API (such as Laser Dance's
FX/Laser), the invalid depth causes the shader to treat virtual objects near the player as occluded
by the physical environment, making objects (e.g. laser beams) disappear when the player approaches
them. Rewriting EnvironmentDepthManager methods to return false / None in libil2cpp.so prevents depth
occlusion keywords (HARD_OCCLUSION / SOFT_OCCLUSION) from ever being enabled, keeping virtual objects
visible at all distances.
"""
from __future__ import annotations

import struct

try:
    import lz4.block
except ImportError:
    lz4 = None

from ..base import ApkContext, Suggestion, register
from .unity_text_input import RET_FALSE, Il2cppReturnPatch

RET_ZERO = RET_FALSE  # mov w0, #0 ; ret


def _patch_shader_proximity_fade(data: bytes, is_known_build: bool = True) -> tuple[bytes | None, int]:
    """Scan asset data for LZ4-compressed shader blobs that contain distance proximity fade
    factors (such as (dist - 2.0) * 0.5 in Laser Dance FX/Laser Beam Hard Edges)
    and neutralise the proximity fade factor so objects/lasers stay visible at all distances.
    Limited to known builds (e.g. Laser Dance) and shader blobs containing proximity fade/Laser signatures.
    """
    if not lz4 or not is_known_build:
        return None, 0

    c_2_5 = struct.pack('<f', -2.5)  # b'\x00\x00\x20\xc0'
    c_2_0 = struct.pack('<f', -2.0)  # b'\x00\x00\x00\xc0'
    c_10_0 = struct.pack('<f', 10.0) # b'\x00\x00\x20\x41'

    ba = bytearray(data)
    total_patched = 0
    p = 0
    data_len = len(ba)

    while p < data_len - 8:
        decomp_sz, comp_sz = struct.unpack_from('<II', ba, p)
        if (1000 < decomp_sz < 10000000 and 100 < comp_sz < 5000000
                and decomp_sz > comp_sz and p + 8 + comp_sz <= data_len):
            try:
                raw = lz4.block.decompress(ba[p+8:p+8+comp_sz], uncompressed_size=decomp_sz)
                # Limit rewriting to known shader signatures/keywords (e.g. Laser Dance FX/Laser or test signatures)
                if not any(sig in raw for sig in (b"Laser", b"laser", b"FX/", b"Proximity", b"SMOL")):
                    p += 4
                    continue
                if c_2_5 in raw and c_2_0 in raw:
                    raw_ba = bytearray(raw)
                    count = struct.unpack('<I', raw_ba[:4])[0]
                    if 1 <= count <= 500:
                        entries = [struct.unpack('<III', raw_ba[4+i*12:16+i*12]) for i in range(count)]
                        patched_subs = 0
                        for off, length, _seg in entries:
                            sub = raw_ba[off:off+length]
                            if sub.count(c_2_5) == 1 and sub.count(c_2_0) == 1:
                                p20 = sub.find(c_2_0)
                                raw_ba[off+p20:off+p20+4] = c_10_0
                                patched_subs += 1

                        if patched_subs > 0:
                            # Recompress with LZ4 HC Level 10 and adjust to exact comp_sz
                            c10 = lz4.block.compress(
                                bytes(raw_ba), mode='high_compression', compression=10, store_size=False
                            )
                            idx = 0
                            decomp_acc = bytearray()
                            seqs = []
                            while idx < len(c10):
                                token = c10[idx]
                                idx += 1
                                lit_len = token >> 4
                                if lit_len == 15:
                                    while c10[idx] == 255:
                                        lit_len += 255
                                        idx += 1
                                    lit_len += c10[idx]
                                    idx += 1
                                lits = c10[idx:idx+lit_len]
                                idx += lit_len
                                decomp_acc.extend(lits)
                                if idx >= len(c10):
                                    seqs.append([bytearray(lits), 0, 0, bytearray()])
                                    break
                                offset = c10[idx] | (c10[idx+1] << 8)
                                idx += 2
                                match_len = (token & 0x0f) + 4
                                if (token & 0x0f) == 15:
                                    while c10[idx] == 255:
                                        match_len += 255
                                        idx += 1
                                    match_len += c10[idx]
                                    idx += 1
                                mb = bytearray()
                                for _ in range(match_len):
                                    b = decomp_acc[-offset]
                                    decomp_acc.append(b)
                                    mb.append(b)
                                seqs.append([bytearray(lits), offset, match_len, mb])

                            def encode_seqs(s_list):
                                out = bytearray()
                                for i, s in enumerate(s_list):
                                    is_last = (i == len(s_list) - 1)
                                    lit_len = len(s[0])
                                    mlen = 0 if is_last else s[2]
                                    m_code = 0 if is_last else min(mlen - 4, 15)
                                    out.append((min(lit_len, 15) << 4) | m_code)
                                    if lit_len >= 15:
                                        rem = lit_len - 15
                                        while rem >= 255:
                                            out.append(255)
                                            rem -= 255
                                        out.append(rem)
                                    out.extend(s[0])
                                    if not is_last:
                                        out.append(s[1] & 0xff)
                                        out.append((s[1] >> 8) & 0xff)
                                        if mlen - 4 >= 15:
                                            rem = mlen - 4 - 15
                                            while rem >= 255:
                                                out.append(255)
                                                rem -= 255
                                            out.append(rem)
                                return bytes(out)

                            curr_enc = encode_seqs(seqs)
                            i = 0
                            while len(curr_enc) < comp_sz and i < len(seqs) - 1:
                                s = seqs[i]
                                ns = seqs[i+1]
                                if s[2] == 4 and len(s[0]) < 10 and len(ns[0]) < 10:
                                    ns[0] = s[0] + s[3] + ns[0]
                                    seqs.pop(i)
                                    curr_enc = encode_seqs(seqs)
                                else:
                                    i += 1

                            if len(curr_enc) == comp_sz:
                                verify = lz4.block.decompress(curr_enc, uncompressed_size=decomp_sz)
                                if verify == raw_ba:
                                    ba[p+8:p+8+comp_sz] = curr_enc
                                    total_patched += patched_subs
                                    p = (p + 8 + comp_sz + 3) & ~3
                                    continue
            except Exception:
                pass
        p += 4

    if total_patched > 0:
        return bytes(ba), total_patched
    return None, 0


LASER_DANCE = "com.mrf.tvb.laser_dance"
# Laser Dance's asset fixes, file -> [(offset, struct format, original value, new value)]. The original values at all
# of these places together identify the build they were made for (no versionName was recorded with them): another
# build doesn't match all of them and gets none of the asset fixes.
_LD_SPLIT0 = "sharedassets0.assets.split0"
_LD_SPLIT79 = "sharedassets0.assets.split79"
_LD_SPLIT80 = "sharedassets0.assets.split80"
LASER_DANCE_ASSETS = {
    # Material 97 (LaserBeam): shader 281 (FX/Laser Beam Hard Edges) -> 279 (Custom/UnlitColorInstancedFading)
    _LD_SPLIT0: [(0x7B2A0, "<Iq", (0, 281), (0, 279))],
    # LineRenderers 5495 and 5497 (beam_LOD0): material 97 -> 45 (LaserBeam with shader 279)
    _LD_SPLIT79: [(0x20A38, "<Iq", (0, 97), (0, 45)), (0x20D78, "<Iq", (0, 97), (0, 45))],
    # LineLod 13814 (rules 0/1) and 13936 (rules 4/5): LOD0 at every distance
    _LD_SPLIT80: [(0x9958 + 52, "<f", 2.30, 999.0), (0x9958 + 124, "<f", 2.30, 0.0),
                  (0xEB40 + 360, "<f", 2.20, 9999.0), (0xEB40 + 432, "<f", 2.20, 0.0)],
}
_LD_NOTES = {_LD_SPLIT0: "redirected Material 97 shader from 281 to 279",
             _LD_SPLIT79: "redirected {n} LineRenderer materials from 97 to 45",
             _LD_SPLIT80: "adjusted {n} LineLod distance rules to maintain close-range laser visibility"}


def _ld_key(name: str) -> str | None:
    return next((k for k in LASER_DANCE_ASSETS if name.endswith(k)), None)


def _ld_matches(fmt: str, data: bytes, off: int, want) -> bool:
    size = struct.calcsize(fmt)
    if off + size > len(data):
        return False
    got = struct.unpack_from(fmt, data, off)
    if fmt == "<f":
        return round(got[0], 2) == want
    return got == want


def _patch_laser_dance_assets(name: str, data: bytes) -> tuple[bytes | None, str | None]:
    """One Laser Dance asset file: the fixes in LASER_DANCE_ASSETS whose original values are in place."""
    key = _ld_key(name)
    if not key:
        return None, None
    ba = bytearray(data)
    n = 0
    for off, fmt, old, new in LASER_DANCE_ASSETS[key]:
        if _ld_matches(fmt, ba, off, old):
            struct.pack_into(fmt, ba, off, *(new if isinstance(new, tuple) else (new,)))
            n += 1
    return (bytes(ba), _LD_NOTES[key].format(n=n)) if n else (None, None)


def laser_dance_build(ws) -> dict[str, str] | None:
    """The asset files of the Laser Dance build the fixes were made for ({key: entry name}), else None."""
    found = {}
    for name in ws.names():
        key = _ld_key(name)
        if key:
            found[key] = name
    if set(found) != set(LASER_DANCE_ASSETS):
        return None
    for key, name in found.items():
        data = ws.read(name)
        if not all(_ld_matches(fmt, data, off, old) for off, fmt, old, _new in LASER_DANCE_ASSETS[key]):
            return None
    return found


KEYWORDS = (b"HARD_OCCLUSION", b"SOFT_OCCLUSION")
RENAMED = (b"NO_OCCLUSION_X", b"NO_OCCLUSION_Y")  # same length: the files keep their size and offsets
MAX_REWRITE = 512 << 20  # asset files larger than this are left alone (they're read into memory to be rewritten)


def _is_asset(name: str) -> bool:
    return name.endswith(".assets") or ".assets.split" in name


class MetaDepthOcclusion(Il2cppReturnPatch):
    id = "frame.meta_depth_occlusion"
    title = "Unity: disable Meta depth occlusion"
    description = (
        "The Frame has no depth sensor (XR_META_environment_depth), so the environment depth Meta's Depth API reads "
        "is never valid. Shaders using it then hide virtual objects near the player behind a room that isn't there "
        "(for example Laser Dance's lasers vanish when approached). Rewrites EnvironmentDepthManager's support/"
        "availability checks and its occlusion-keyword setter in libil2cpp.so (found with Cpp2IL), and renames the "
        "HARD_OCCLUSION / SOFT_OCCLUSION shader keywords in the game's asset files that contain them, so occlusion is "
        "never turned on. For Laser Dance's known build it also keeps the laser beams' materials and close-range "
        "LODs visible. Only for games with this symptom."
    )
    order = 48
    targets = {
        "Meta.XR.EnvironmentDepth/Meta/XR/EnvironmentDepth/EnvironmentDepthManager.cs": {
            "get_IsSupported": RET_ZERO,
            "get_IsDepthAvailable": RET_ZERO,
            "get_OcclusionShadersMode": RET_ZERO,
            "SetOcclusionShaderKeywords": RET_ZERO,
        },
        "Assembly-CSharp/EnvironmentDepthManager.cs": {
            "get_IsSupported": RET_ZERO,
            "get_IsDepthAvailable": RET_ZERO,
            "get_OcclusionShadersMode": RET_ZERO,
            "SetOcclusionShaderKeywords": RET_ZERO,
        },
    }
    check_name = "Meta depth occlusion"

    def applies(self, a):
        return a.engine == "Unity" and "libil2cpp.so" in a.libs and not a.only_32bit

    def detect(self, a):
        if self.applies(a) and (a.extra or {}).get("meta_depth_occlusion"):
            # many Meta XR SDK games carry the Depth API without using it: only for a game with the symptom
            return Suggestion(False, "Uses Meta's Depth API occlusion (HARD_OCCLUSION / SOFT_OCCLUSION): turn on if "
                                     "objects vanish when you come close to them.")
        return None

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        il2cpp_ok = super().apply(ctx)

        laser_dance = ws and getattr(ctx.analysis, "package", "") == LASER_DANCE and laser_dance_build(ws)
        if getattr(ctx.analysis, "package", "") == LASER_DANCE and not laser_dance:
            ctx.notes.append("Laser Dance asset fixes skipped: not the build they were made for")

        renamed_files = renamed = shader_files = shader_subs = ld_files = 0
        for name in sorted(ws.names()):
            if not _is_asset(name):
                continue
            ld = bool(laser_dance)
            # files without the keywords are only streamed, never held in memory (except Laser Dance's known build)
            has_keywords = ws.contains(name, KEYWORDS)
            if not (has_keywords or ld):
                continue
            if ws.size(name) > MAX_REWRITE:
                ctx.notes.append(f"{name}: too large to rewrite, occlusion keywords left as they are")
                continue
            data = ws.read(name)
            modified = False
            if ld and _ld_key(name):
                new, note = _patch_laser_dance_assets(name, data)
                if new is not None:
                    data, modified = new, True
                    ld_files += 1
                    ctx.notes.append(f"{name}: {note}")
            if ld:
                new, subs = _patch_shader_proximity_fade(data, is_known_build=True)
                if new is not None and subs:
                    data, modified = new, True
                    shader_files += 1
                    shader_subs += subs
                    ctx.notes.append(f"{name}: neutralized distance proximity fade in {subs} shader subprograms")
            counts = [data.count(k) for k in KEYWORDS]
            if any(counts):
                for k, r in zip(KEYWORDS, RENAMED, strict=True):
                    data = data.replace(k, r)
                renamed_files += 1
                renamed += sum(counts)
                ctx.notes.append(f"{name}: disabled {counts[0]} HARD_OCCLUSION and {counts[1]} SOFT_OCCLUSION "
                                 "keywords")
                modified = True
            if modified:
                ws.put(name, data)

        if renamed_files:
            ctx.reporter.log(f"Meta depth occlusion: neutralised {renamed} occlusion keywords across "
                             f"{renamed_files} files")
        if shader_files:
            ctx.reporter.log(f"Meta depth occlusion: neutralised proximity fade in {shader_subs} shader subprograms "
                             f"across {shader_files} files")
        if ld_files:
            ctx.reporter.log(f"Meta depth occlusion: applied Laser Dance laser asset fixes to {ld_files} files")
        return bool(il2cpp_ok or renamed_files or shader_files or ld_files)


register(MetaDepthOcclusion)
