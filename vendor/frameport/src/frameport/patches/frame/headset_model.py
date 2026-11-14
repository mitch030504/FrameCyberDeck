"""Per-game OpenXR headset identity (GitHub #373)."""
from __future__ import annotations

import struct

from ..base import Param, Patch, Suggestion, register

# Quest 2 deliberately leaves both layers untouched: the compatibility escape hatch.
MODELS = {"": (3, "Quest 3"), "quest3s": (4, "Quest 3S"),
          "questpro": (5, "Quest Pro"), "quest2": (2, "Quest 2")}


# Raw XrUuidEXT bytes, in comparison order in OVRPlugin 0x292f80 (not GUID field-endian order).
UUIDS = {"quest2": bytes.fromhex("3064f23915ed4722a6595ea1ecd899c2"),
         "questpro": bytes.fromhex("b58443c9df8640cd89182a278e18ab71"),
         "": bytes.fromhex("082746baa0714cf78f87bade5cce43ce"),
         "quest3s": bytes.fromhex("9510492b0d424872bcf4fb1a8def6f0e")}
# "Meta Quest Pro" plus NUL exceeds the original 14-byte slot. Its UUID selects Pro; use a short label.
SYSTEM_NAMES = {"": b"Meta Quest 3", "quest3s": b"Meta Quest 3S",
                "questpro": b"Quest Pro", "quest2": b"Oculus Quest2"}
# Verified OVRPort 3.4.3-aa54c3f arm64 dispatcher: file offsets (VAs differ).
# Guard both data and the instructions that load those exact answers, before changing either. Other runtimes (the
# older 3.4.3-23204ea, later ones) are matched by structure instead (find_identity).
NAME_OFFSET, UUID_OFFSET = 0x6034, 0x21338
GUARDS = {0x18620: bytes.fromhex("61ffffd021d00091807200910220805203208052c401805262210094"),
          0x186e4: bytes.fromhex("1f2003d584620810"),
          0x18778: bytes.fromhex("8000c03de006803de2ffff17")}


QUEST2_NAME = b"Oculus Quest2\0"


def _code_references(data: bytes) -> dict[int, int]:
    """{address: number of loads} for the addresses AArch64 code forms with `adr`, or `adrp` + `add` / `ldr q` (the
    two ways the verified dispatcher loads systemName and the UUID), over the executable PT_LOAD segments."""
    from ...analysis import elf

    refs: dict[int, int] = {}
    for seg in elf._elf(data).iter_segments():
        if seg["p_type"] != "PT_LOAD" or not seg["p_flags"] & 1:
            continue
        va0, off, size = seg["p_vaddr"], seg["p_offset"], seg["p_filesz"]
        words = struct.unpack_from(f"<{size // 4}I", data, off)
        for i, w in enumerate(words):
            if w & 0x1F000000 != 0x10000000:  # adr / adrp
                continue
            imm = ((w >> 5) & 0x7FFFF) << 2 | (w >> 29) & 3
            imm -= (imm & 1 << 20) << 1
            pc = va0 + 4 * i
            if not w & 0x80000000:  # adr
                refs[pc + imm] = refs.get(pc + imm, 0) + 1
                continue
            rd, page = w & 0x1F, (pc & ~0xFFF) + (imm << 12)
            for x in words[i + 1:i + 9]:
                if (x >> 5) & 0x1F != rd:
                    continue
                if x & 0xFFC00000 == 0x91000000:  # add xD, xN, #imm12
                    target = page + ((x >> 10) & 0xFFF)
                elif x & 0xFFC00000 == 0x3DC00000:  # ldr qT, [xN, #imm12 * 16]
                    target = page + ((x >> 10) & 0xFFF) * 16
                else:
                    continue
                refs[target] = refs.get(target, 0) + 1
                break
    return refs


def find_identity(data: bytes) -> tuple[int, int] | None:
    """(systemName file offset, UUID file offset) of an OVRPort dispatcher: the verified 3.4.3-aa54c3f layout, else
    found by structure for other runtimes: Quest 2's name and UUID each exactly once in the library, and each loaded
    by exactly one instruction sequence of its code. None = not found (nothing is changed)."""
    expected = {NAME_OFFSET: QUEST2_NAME, UUID_OFFSET: UUIDS["quest2"], **GUARDS}
    if all(data[off:off + len(value)] == value for off, value in expected.items()):
        return NAME_OFFSET, UUID_OFFSET
    from ...analysis import elf

    if not (elf.is_64bit(data) and data[5] == 1 and data[18:20] == b"\xb7\x00"):  # little-endian AArch64 only
        return None
    if data.count(QUEST2_NAME) != 1 or data.count(UUIDS["quest2"]) != 1:
        return None
    try:
        segments = elf.load_segments(data)
        refs = _code_references(data)
    except Exception:  # noqa: BLE001 - unreadable ELF: refuse
        return None

    def address(off: int) -> int | None:
        return next((va + off - o for va, o, size in segments if o <= off < o + size), None)

    name_off, uuid_off = data.find(QUEST2_NAME), data.find(UUIDS["quest2"])
    if any(refs.get(address(off)) != 1 for off in (name_off, uuid_off)):
        return None
    return name_off, uuid_off


def patch_dispatcher(data: bytes, key: str) -> bytes:
    """Same-size data-only edit; unknown dispatchers fail atomically. Quest 2 is an exact no-op."""
    if key not in SYSTEM_NAMES:
        raise ValueError(f"Unknown headset model: {key!r}")
    if key == "quest2":
        return data
    found = find_identity(data)
    if not found:
        raise ValueError("Unsupported OVRPort headset identity dispatcher: its Quest 2 systemName/UUID and the code "
                         "that loads them weren't found; select Quest 2 to preserve its identity.")
    name_off, uuid_off = found
    out = bytearray(data)
    out[name_off:name_off + 14] = SYSTEM_NAMES[key].ljust(14, b"\0")
    out[uuid_off:uuid_off + 16] = UUIDS[key]
    return bytes(out)


def model(params):
    key = (params or {}).get("model", "")
    if key not in MODELS:
        raise ValueError(f"Unknown headset model: {key!r}")
    return MODELS[key]


def adapter_settings(recipe_patches, *, defaults=None):
    """Share the device choice with embedded settings and configuration-only updates."""
    from ..settings import adapter_settings as other_settings

    out = other_settings(recipe_patches, defaults=defaults)
    if "device.headset_model" in recipe_patches:
        out["headset_model"] = model(recipe_patches["device.headset_model"])[0]
    return out


class HeadsetModel(Patch):
    id = "device.headset_model"
    title = "Headset model"
    description = ("Choose the OpenXR headset name and headset UUID reported to Quest games. "
                   "Quest 3 is the default; Quest 2 preserves OVRPort's original answers. "
                   "Requires a rebuild. Android Build.MODEL remains Lepton.")
    category = "device"
    stage = "apk"
    order = 12
    default_on = True
    summary = "Choose the headset identity the game sees; Quest 3 is the default."
    CHOICES = (("", "Quest 3 (default)"), ("quest3s", "Quest 3S"), ("questpro", "Quest Pro"), ("quest2", "Quest 2"))
    params = [Param("model", "str", "", "Headset model reported to the game.")]

    def applies(self, a):
        return a.vr_kind == "quest" and (a.extra or {}).get("kind") not in ("rift", "linux")

    def detect(self, a):
        if self.applies(a):
            return Suggestion(True, "Quest 3 hardware is closest to Steam Frame: report Quest 3 by default.")
        return None

    def apply(self, ctx):
        if not self.applies(ctx.analysis):
            return False
        key = (ctx.params or {}).get("model", "")
        model(ctx.params)  # validate before even a missing-library no-op
        ws = ctx.ws
        loader = "libopenxr_loader.so"
        if key == "quest2":
            return False
        if not ws.has(ws.lib(loader)):
            ctx.checks.append((self.id, None, "No OVRPort dispatcher; only FrameBridge's systemName override applies."))
            return False
        try:
            fixed = patch_dispatcher(ws.read_lib(loader), key)
        except ValueError:
            # Quest 3 is everyone's default: an APK converted with another OVRPort runtime (e.g. an already converted
            # copy) keeps OVRPort's Quest 2 answers instead of failing its build
            ctx.checks.append((self.id, None, "This OVRPort dispatcher's headset identity (Quest 2 name and UUID "
                                              "and the code that loads them) wasn't found; the game keeps seeing a "
                                              "Quest 2 (only FrameBridge's systemName override applies)."))
            return False
        ws.put(ws.lib(loader), fixed)
        ctx.notes.append(f"{loader}: systemName={SYSTEM_NAMES[key].decode()}, HeadsetId={UUIDS[key].hex()}")
        return True



def migrate(data):
    """Once per library, include existing Quest recipes in the new default."""
    for game in (data.get("games") or {}).values():
        analysis, recipe = game.get("analysis"), game.get("recipe")
        if not isinstance(analysis, dict) or not isinstance(recipe, dict):
            continue
        extra = analysis.get("extra") or {}
        if (game.get("kind") in ("rift", "linux") or extra.get("kind") in ("rift", "linux")
                or extra.get("vr_kind", "quest") != "quest"):
            continue
        recipe.setdefault("patches", {}).setdefault(HeadsetModel.id, {})
        recipe.setdefault("reasons", {}).setdefault(HeadsetModel.id, "Report Quest 3 by default.")


register(HeadsetModel)
