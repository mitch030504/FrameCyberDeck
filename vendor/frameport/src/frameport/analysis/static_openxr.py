"""Khronos loaders embedded in game libraries (never the separately shipped loader)."""
from __future__ import annotations

import io
import re

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from . import elf

MANIFEST = "/sdcard/fp_openxr_runtime.json"
# Some builds embed complete paths. Match every partition and ABI spelling, rather than a fixed list.
PATH = re.compile(rb"/(?:[A-Za-z0-9_-]+/)*etc/openxr/1/active_runtime(?:\.[A-Za-z0-9_-]+)?\.json\x00")
COMPOSED_MANIFEST = "/sdcard/fp/1/active_runtime.json"
PARTITIONS = ("/product", "/odm", "/oem", "/vendor", "/system")
SUBDIR = "/etc/openxr/"
BROKERS = ("org.khronos.openxr.runtime_broker", "org.khronos.openxr.system_runtime_broker")
# Android's implicit API layer folders are <partition>/etc + openxr/1 + this suffix. FrameBridge's own (inner) loader
# loads them; the game's loader must not, or Valve's layer is stacked twice in one process and its two chains call
# each other until the stack runs out (GitHub #254).
IMPLICIT_LAYERS = "/api_layers/implicit.d"
NO_IMPLICIT_LAYERS = "/api_layers/fp_none.d"


def paths(data: bytes) -> list[str]:
    if not elf.is_elf(data):
        return []
    sec = ELFFile(io.BytesIO(data)).get_section_by_name(".rodata")
    if sec is None:
        return []
    raw = sec.data()
    return sorted({m.group()[:-1].decode() for m in PATH.finditer(raw)
                   if m.start() == 0 or raw[m.start() - 1] == 0})


def detected(name: str, data: bytes) -> bool:
    if (name.startswith("libopenxr_loader") or not elf.is_elf(data)
            or b"RuntimeManifestFile" not in data or b"xrNegotiateLoaderRuntimeInterface" not in data):
        return False
    try:
        e = ELFFile(io.BytesIO(data))
        return e["e_machine"] == "EM_AARCH64" and (bool(paths(data)) or composed(data))
    except (ELFError, ValueError):
        return False


def composed(data: bytes) -> bool:
    """Khronos platform_utils.hpp builds prefix + subdir + major + suffix.

    Require the complete known table before touching these potentially shared literals.
    """
    sec = ELFFile(io.BytesIO(data)).get_section_by_name(".rodata")
    strings = set(sec.data().split(b"\0")) if sec else set()
    return (all(s.encode() in strings for s in (*PARTITIONS, SUBDIR, "/active_runtime.json"))
            and any(s.startswith(b"/active_runtime.") and s.endswith(b".json") for s in strings))


def redirected(data: bytes) -> bool:
    if (paths(data) or composed(data) or any(b.encode() + b"\0" in data for b in BROKERS)
            or implicit_layers(data)):
        return False
    if MANIFEST.encode() + b"\0" in data:
        return True
    sec = ELFFile(io.BytesIO(data)).get_section_by_name(".rodata")
    strings = set(sec.data().split(b"\0")) if sec else set()
    return (b"card/fp/" in strings and b"/sd" in strings
            and not any(p.encode() in strings for p in PARTITIONS))


def implicit_layers(data: bytes) -> bool:
    sec = ELFFile(io.BytesIO(data)).get_section_by_name(".rodata")
    return bool(sec) and IMPLICIT_LAYERS.encode() in set(sec.data().split(b"\0"))


def rewrite(data: bytes) -> tuple[bytes, int]:
    count = 0
    if composed(data):
        # Keep every candidate identical: /sd + card/fp/ + 1 + /active_runtime[.ABI].json.
        # These partition literals may be pooled with app strings; see DEVICE_NOTES.
        for old, new in [(p, "/sd") for p in PARTITIONS] + [(SUBDIR, "card/fp/")]:
            data, n = elf.replace_rodata_string(data, old, new)
            count += n
    for old in paths(data):
        data, n = elf.replace_rodata_string(data, old, MANIFEST)
        count += n
    for old in BROKERS:
        # Keep URI length unchanged as well as the string's storage; both authorities must miss.
        data, n = elf.replace_rodata_string(data, old, old.replace("org.khronos", "org.fp_null"))
        count += n
    data, n = elf.replace_rodata_string(data, IMPLICIT_LAYERS, NO_IMPLICIT_LAYERS)
    return data, count + n
