"""Just Dance VR (com.ubisoft.justdancevr): write the local save that the game already reads (GitHub #480).

Every system of the game (player progression, inventory, avatar, goals, achievements, tutorial, emotes and toys,
language, song of the week) saves through `Neon.Gameplay.LocalSave.LocalSaveEvents.RequestSave(key, value)` and, at
the next start, loads that key back through `LocalSaveEvents.RequestHasSaveData` / `RequestLoad` before it syncs
with Ubisoft's PlayFab backend. In 0.5.4 RequestSave's body is empty (a single `ret` in libil2cpp.so), so the
`SaveValueRequested` event that `LocalSaveManager.OnSaveValueRequested` listens to is never raised,
`LocalSaveController.SaveData` never reaches `JdOfflineSave.OfflineSave_SetValue`, the offline save file is never
marked for writing and each start finds nothing to load (`OfflineSaveDico_FromString - NeedLoad: False`). On a Quest
the progress lives in PlayFab only (a login with the Meta account's user id); without Meta's platform the game never
signs in, so nothing is kept anywhere. Nothing here signs in or touches the online side.

The fix gives RequestSave the body it is declared with: `SaveValueRequested?.Invoke(new LocalSaveEventArgs(key,
value))`. Everything is found per build, by name and by structure:
- Cpp2IL (the shared run of every IL2CPP patch) gives RequestSave, its neighbour SaveSystemReady, the static field
  offsets of SaveValueRequested and SaveSystemReadyEvent, and LocalSaveEventArgs' fields (Key at 0, Value at 8: a
  16-byte struct, passed in two registers).
- SaveSystemReady (`SaveSystemReadyEvent?.Invoke()`) is decoded with capstone: the GOT slot of LocalSaveEvents'
  TypeInfo variable, the runtime-metadata init it calls on that slot, the class's static-fields offset, and the
  delegate's invoke_impl / target / method offsets. The new body repeats exactly that, for the other field and with
  key/value as arguments.
- The 24 new instructions go into a new executable PT_LOAD segment appended to libil2cpp.so (elf.append_segment;
  nothing that exists moves), and RequestSave's `ret` becomes a branch there.

Builds whose RequestSave already has a body (the game saves by itself) are left unchanged; anything that can't be
found or decoded as expected is refused with the reason, never guessed. An already patched library is recognised
(RequestSave branches to exactly the code this patch writes)."""
from __future__ import annotations

import struct

from ...analysis import elf
from ..base import ApkContext, Suggestion, register
from .unity_text_input import ALL_TARGETS, METADATA, Il2cppReturnPatch, can_rewrite_il2cpp

PACKAGE = "com.ubisoft.justdancevr"
LIB = "libil2cpp.so"
RET = 0xD65F03C0
EVENTS = "Neon/Neon/Gameplay/LocalSave/LocalSaveEvents.cs"
ARGS = "Neon/Neon/Gameplay/LocalSave/LocalSaveEventArgs.cs"
WANTED = {EVENTS: ["RequestSave", "SaveSystemReady", "field:SaveValueRequested", "field:SaveSystemReadyEvent"],
          ARGS: ["field:Key", "field:Value"]}
for _cls, _names in WANTED.items():  # one Cpp2IL run (and cache entry) serves every IL2CPP patch
    ALL_TARGETS.setdefault(_cls, [])
    ALL_TARGETS[_cls] += [n for n in _names if n not in ALL_TARGETS[_cls]]
BODY_WORDS = 24



# ---- AArch64 encoders (only what the new body needs)
def _b(src: int, dst: int, link: bool = False) -> int:
    off = dst - src
    if off % 4 or not -(1 << 27) <= off < (1 << 27):
        raise ValueError(f"branch from {src:#x} to {dst:#x} out of range")
    return (0x94000000 if link else 0x14000000) | ((off >> 2) & 0x3FFFFFF)


def _cbz_x(src: int, dst: int, rt: int) -> int:
    off = dst - src
    assert off % 4 == 0 and -(1 << 20) <= off < (1 << 20)
    return 0xB4000000 | ((off >> 2) & 0x7FFFF) << 5 | rt


def _adrp(src: int, page: int, rd: int) -> int:
    imm = (page >> 12) - (src >> 12)
    if not -(1 << 20) <= imm < (1 << 20):
        raise ValueError(f"adrp from {src:#x} to {page:#x} out of range")
    return 0x90000000 | (imm & 3) << 29 | ((imm >> 2) & 0x7FFFF) << 5 | rd


def _ldr_x(rt: int, rn: int, off: int) -> int:
    assert off % 8 == 0 and 0 <= off < 0x8000
    return 0xF9400000 | (off // 8) << 10 | rn << 5 | rt


def _mov_x(rd: int, rm: int) -> int:
    return 0xAA0003E0 | rm << 16 | rd


def _br(rn: int) -> int:
    return 0xD61F0000 | rn << 5


PUSH = (0xF81E0FFE, 0xA9014FF4)  # str x30, [sp, #-0x20]! ; stp x20, x19, [sp, #0x10]
POP = (0xA9414FF4, 0xF84207FE)   # ldp x20, x19, [sp, #0x10] ; ldr x30, [sp], #0x20


class Refused(Exception):
    """The code isn't what this patch expects: nothing is changed."""


def decode_template(code: bytes, va: int, event_field: int) -> dict:
    """What SaveSystemReady (`SaveSystemReadyEvent?.Invoke()`, an Action) does, from its instructions:
    {"slot": VA of the GOT slot with &LocalSaveEvents_TypeInfo_var, "init": the metadata init called on it,
     "statics": Il2CppClass static_fields offset, "impl"/"target"/"method": the delegate's invoke_impl, the target
     passed in x0 and the MethodInfo passed in x1}."""
    import capstone
    from capstone import arm64_const as A

    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    md.detail = True
    reg: dict[int, tuple] = {}  # register id -> ("page", va) | ("slot", va) | ("class",) | ("statics",) | ("delegate",)
    out: dict = {}
    loads: dict[int, int] = {}  # register loaded from the delegate -> field offset
    last_x0_slot = None
    for ins in md.disasm(code, va):
        ops = ins.operands
        if ins.id == A.ARM64_INS_ADRP:
            reg[ops[0].reg] = ("page", ops[1].imm)
            last_x0_slot = None
            continue
        if ins.id == A.ARM64_INS_LDR and len(ops) == 2 and ops[1].type == A.ARM64_OP_MEM \
                and ops[1].mem.index == 0 and not ins.writeback:
            dst, base, disp = ops[0].reg, ops[1].mem.base, ops[1].mem.disp
            src = reg.get(base)
            new = None
            if src and src[0] == "page":
                new = ("slot", src[1] + disp)
            elif src and src[0] == "slot" and disp == 0:
                new = ("class", src[1])
            elif src and src[0] == "class":
                out["statics"], new = disp, ("statics", src[1])
            elif src and src[0] == "statics" and disp == event_field:
                new = ("delegate", src[1])
            elif src and src[0] == "delegate":
                loads[dst] = disp
            if new:
                reg[dst] = new
                if new[0] == "class":
                    out.setdefault("slot", new[1])
                    if out["slot"] != new[1]:
                        raise Refused("SaveSystemReady reads two different class slots")
            last_x0_slot = new[1] if new and new[0] == "slot" and dst == A.ARM64_REG_X0 else None
            continue
        if ins.id == A.ARM64_INS_BL and last_x0_slot is not None:
            out.setdefault("init", ops[0].imm)
            out.setdefault("init_slot", last_x0_slot)
        if ins.id in (A.ARM64_INS_BR, A.ARM64_INS_BLR) and ops[0].reg in loads:
            out["impl"] = loads[ops[0].reg]
            out["target"] = loads.get(A.ARM64_REG_X0)
            out["method"] = loads.get(A.ARM64_REG_X1)
            break
        last_x0_slot = None
    missing = [k for k in ("slot", "init", "statics", "impl", "target", "method") if out.get(k) is None]
    if missing:
        raise Refused(f"SaveSystemReady's code isn't the expected event call (no {', '.join(missing)})")
    if out.pop("init_slot") != out["slot"]:
        raise Refused("SaveSystemReady initialises another slot than it reads")
    return out


def request_save_body(at_va: int, t: dict, event_field: int) -> list[int]:
    """LocalSaveEvents.RequestSave(string key, object value) = SaveValueRequested?.Invoke(new LocalSaveEventArgs(key,
    value)), built like SaveSystemReady (t = decode_template's result). LocalSaveEventArgs is a 16-byte struct (Key,
    Value) passed in two registers, so invoke_impl gets (target, key, value, method)."""
    w: list[int] = []

    def at() -> int:
        return at_va + 4 * len(w)

    page, lo = t["slot"] & ~0xFFF, t["slot"] & 0xFFF
    w += PUSH
    w += [_mov_x(19, 0), _mov_x(20, 1)]          # key, value
    w += [_adrp(at(), page, 0)]
    w += [_ldr_x(0, 0, lo), _b(at() + 4, t["init"], link=True)]  # initialise the TypeInfo slot (idempotent)
    w += [_adrp(at(), page, 8)]
    w += [_ldr_x(8, 8, lo),                       # &LocalSaveEvents_TypeInfo_var
          _ldr_x(8, 8, 0),                        # Il2CppClass*
          _ldr_x(8, 8, t["statics"]),             # static_fields
          _ldr_x(8, 8, event_field)]              # SaveValueRequested
    end = at_va + 4 * 21
    w += [_cbz_x(at(), end, 8)]
    w += [_ldr_x(4, 8, t["impl"]), _ldr_x(0, 8, t["target"]), _ldr_x(3, 8, t["method"]),
          _mov_x(1, 19), _mov_x(2, 20)]
    w += POP + (_br(4),)
    assert at() == end
    w += POP + (RET,)
    assert len(w) == BODY_WORDS
    return w


def _pack(words: list[int]) -> bytes:
    return struct.pack(f"<{len(words)}I", *words)


def _branch_target(word: int, va: int) -> int | None:
    if word & 0xFC000000 != 0x14000000:
        return None
    imm = word & 0x3FFFFFF
    return va + ((imm - (1 << 26) if imm & (1 << 25) else imm) << 2)


def inspect(lib: bytes, found: dict) -> dict:
    """found = il2cpp.find_methods' result for WANTED. -> {"state": "needed" | "done" | "saves", ...}: "needed" with
    what the edit needs, "done" (RequestSave already branches to this patch's code), "saves" (RequestSave has a body
    of its own: nothing to do). Raises Refused when something can't be found or decoded as expected."""
    events, args = found.get(EVENTS, {}), found.get(ARGS, {})
    names = {"RequestSave": events.get("RequestSave"), "SaveSystemReady": events.get("SaveSystemReady"),
             "SaveValueRequested": events.get("field:SaveValueRequested"),
             "SaveSystemReadyEvent": events.get("field:SaveSystemReadyEvent"),
             "LocalSaveEventArgs.Key": args.get("field:Key"), "LocalSaveEventArgs.Value": args.get("field:Value")}
    missing = [n for n, v in names.items() if v is None]
    if missing:
        raise Refused(f"not found in this build: {', '.join(missing)}")
    if (names["LocalSaveEventArgs.Key"][0], names["LocalSaveEventArgs.Value"][0]) != (0, 8):
        raise Refused("LocalSaveEventArgs isn't the 16-byte (Key, Value) struct this patch passes")
    if not elf.is_elf(lib) or not elf.is_64bit(lib) or struct.unpack_from("<H", lib, 18)[0] != 183:  # AArch64
        raise Refused("libil2cpp.so isn't a 64-bit ARM library")
    segments = elf.load_segments(lib)
    exec_ranges = [(o, o + s["p_filesz"], s["p_vaddr"]) for s in elf._elf(lib).iter_segments()
                   if s["p_type"] == "PT_LOAD" and s["p_flags"] & 1 for o in (s["p_offset"],)]

    def code_va(off: int, length: int, what: str) -> int:
        if off % 4 or not any(a <= off and off + length <= b for a, b, _v in exec_ranges):
            raise Refused(f"{what} at {off:#x} isn't code in libil2cpp.so (Cpp2IL mismatch)")
        return elf.offset_to_vaddr(segments, off)

    r_off, r_len = names["RequestSave"]
    t_off, t_len = names["SaveSystemReady"]
    r_va, t_va = code_va(r_off, 4, "RequestSave"), code_va(t_off, t_len, "SaveSystemReady")
    event_field = names["SaveValueRequested"][0]
    template = decode_template(lib[t_off:t_off + t_len], t_va, names["SaveSystemReadyEvent"][0])
    info = {"request_save": (r_off, r_va), "template": template, "event_field": event_field}
    word = int.from_bytes(lib[r_off:r_off + 4], "little")
    if r_len == 4 and word == RET:
        return {"state": "needed", **info}
    target = _branch_target(word, r_va)
    if target is not None:
        t_file = elf.vaddr_to_offset(segments, target)
        body = _pack(request_save_body(target, template, event_field))
        if t_file is not None and lib[t_file:t_file + len(body)] == body:
            return {"state": "done", **info}
    if r_len == 4:
        raise Refused(f"RequestSave holds an unexpected instruction ({word:#010x})")
    return {"state": "saves", **info}


def patch_library(lib: bytes, found: dict) -> tuple[bytes | None, dict]:
    """(patched library or None when nothing changes, inspect's result)."""
    info = inspect(lib, found)
    if info["state"] != "needed":
        return None, info
    new, va = elf.append_segment(lib, bytes(4 * BODY_WORDS))
    body = _pack(request_save_body(va, info["template"], info["event_field"]))
    off = elf.vaddr_to_offset(elf.load_segments(new), va)
    r_off, r_va = info["request_save"]
    buf = bytearray(new)
    buf[off:off + len(body)] = body
    buf[r_off:r_off + 4] = struct.pack("<I", _b(r_va, va))
    return bytes(buf), {**info, "body_va": va}


class JustDanceLocalSave(Il2cppReturnPatch):
    id = "frame.justdance_local_save"
    title = "Just Dance VR: keep progress in the local save"
    description = ("Just Dance VR (GitHub #480) loads its progress from a local save at start, but the method that "
                   "writes it (LocalSaveEvents.RequestSave) is empty: on a Quest the progress is kept by "
                   "Ubisoft's online backend after a Meta sign-in, which the Frame doesn't have, so progress, unlocks "
                   "and the finished tutorial were lost at every restart. The method gets the body it is declared "
                   "with (raise the SaveValueRequested event the game's own save system listens to), built from its "
                   "neighbour SaveSystemReady and placed in a new code segment of libil2cpp.so; everything is found "
                   "per build with Cpp2IL (downloaded on first use). A build that already writes its save is left "
                   "unchanged. No sign-in or online data is touched.")
    order = 51  # after every Cpp2IL patch (40-50): the new segment changes the file size, the Cpp2IL cache key
    check_name = "Just Dance VR local save"
    note = "Just Dance VR: LocalSaveEvents.RequestSave raises SaveValueRequested (local save written)"
    reason = ("Just Dance VR's local save is never written (progress only lives online after a Meta sign-in), so "
              "progress is lost at every restart on the Frame.")

    def applies(self, a):
        return a.package == PACKAGE and "arm64-v8a" in a.abis and LIB in a.libs

    def detect(self, a):
        if self.applies(a) and can_rewrite_il2cpp(a):
            return Suggestion(True, self.reason)
        return None

    def apply(self, ctx: ApkContext) -> bool:
        from ...analysis.il2cpp import find_methods

        ws = ctx.ws
        lib_name = ws.lib(LIB)
        if ws.abi != "arm64-v8a" or not ws.has(lib_name) or not ws.has(METADATA):
            return False
        version = (ctx.analysis.extra or {}).get("unity_version") or self._unity_version(ws)
        if not version:
            return self._not_applied(ctx, "unknown Unity version (rescan the game)")
        lib = ws.read(lib_name)
        ctx.reporter.log(f"{self.check_name}: finding the code (Cpp2IL; the first time can take a minute)")
        try:
            found = find_methods(lib, ws.read(METADATA), version, ALL_TARGETS)
            patched, info = patch_library(lib, found)
        except Refused as exc:
            return self._not_applied(ctx, f"{exc}; built without this fix")
        except Exception as exc:  # noqa: BLE001 - optional fix: the game still builds, unchanged
            return self._not_applied(ctx, f"{exc}; built without this fix")
        if info["state"] == "saves":
            ctx.checks.append((self.check_name, True, "not needed: this build's RequestSave writes the save itself"))
            ctx.notes.append(f"{self.id}: RequestSave has a body in this build, left unchanged")
            return False
        ctx.checks.append((self.check_name, True, "RequestSave raises SaveValueRequested"))
        if patched is None:  # already patched
            return False
        ws.put(lib_name, patched)
        ctx.notes.append(f"{self.note} (new code at {info['body_va']:#x})")
        return True


register(JustDanceLocalSave)
