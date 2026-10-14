#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

OVRPORT_APP_VERSION = "1.2.3"
OVRPORT_RUNTIME_VERSION = "3.4.3-23204ea"
OVRPORT_ZIP_URL = "https://github.com/ovrport/app/releases/download/1.2.3/cli-jar.zip"
OVRPORT_ZIP_SHA256 = "3239408ff1e97ad016916825c216dc3016c0ffa7db7c317221ed3f1244441594"

# FramePort ships the FrameBridge adapter prebuilt; SHA-256 is from the
# SHA256SUMS manifest at the pinned commit.
FRAMEPORT_COMMIT = "5424e429e46e53fe51497babac124dd91dbb73bf"
FRAMEPORT_ADAPTER_URL = (
    "https://raw.githubusercontent.com/spoopyghosty0/frameport/"
    + FRAMEPORT_COMMIT
    + "/artifacts/arm64-v8a/libopenxr_loader_generic.so"
)
FRAMEPORT_ADAPTER_SHA256 = "449e13bbe58de6718d4e530177924e09552a95d748a082739899144e2cb3ccf3"

# FramePort's per-game shim libraries and recipes: FramePort by spoopyghosty0
# (https://github.com/spoopyghosty0/frameport), GPLv3 like this project.
FRAMEPORT_ARTIFACTS = {
    # name: sha256 from artifacts/SHA256SUMS at FRAMEPORT_COMMIT
    "libframe_xrshim.so": "8a8f6b1da8952cb3933a5b53558424a5fc56043b2527a16bb6dd62a00d2de01a",
    "libfp_vk.so": "e8c0965c188665a4e565e4a3af983ef1866d1cf9225665cc90d8df286bc96ef3",
    "libVkLayer_fp_shaderfix.so": "5becb96ee86fbda0ce98c0e0fd064f8ba092314aceaa400e3dcc785705dc3c23",
    "libfpg.so": "383054f8b3b41dde76d062c71856cd3163655e50009c08454bc1793f393ead65",
    "libglshim.so": "19ba5ed3c4b780f5ae46251f6958f552acfc5f13c8bb39f995579593fa8990c6",
    "libovrplatformcompat.so": "1feaeafad467c4cafdf2b018a4d84b0bee200c3f711697b4ce97e66a3ba256ca",
    "libvrapi.so": "1d83eb94ec1df44f29a5f525dce4d5b608dbbb13a9c7a5efeb80a4b1dc54a721",
}


def emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def fail(message: str, code: int = 1) -> None:
    emit({"ok": False, "error": message})
    raise SystemExit(code)


def log(message: str) -> None:
    print(f"[Frame Convert] {message}", file=sys.stderr, flush=True)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, destination: pathlib.Path, expected_sha256: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    temp = pathlib.Path(filename)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "FrameCyberDeck/0.1"})
        log(f"Downloading {url}")
        with os.fdopen(descriptor, "wb") as out, urllib.request.urlopen(request, timeout=60) as response:
            shutil.copyfileobj(response, out)
        if expected_sha256:
            actual = sha256(temp)
            if actual != expected_sha256:
                raise RuntimeError(
                    f"SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}"
                )
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def run(args: list[str | pathlib.Path], *, cwd: pathlib.Path | None = None) -> str:
    printable = [str(value) for value in args]
    log("$ " + " ".join(printable))
    result = subprocess.run(
        printable,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout.strip():
        for line in result.stdout.splitlines():
            log(line)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({pathlib.Path(printable[0]).name}, exit {result.returncode}): "
            + result.stdout[-3000:]
        )
    return result.stdout


def tools_root() -> pathlib.Path:
    override = os.environ.get("FRAME_CYBERDECK_TOOLS")
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return pathlib.Path.home() / ".local" / "share" / "frame-cyberdeck" / "tools"


def version_key(path: pathlib.Path) -> tuple[int, ...]:
    values = re.findall(r"\d+", path.name)
    return tuple(int(x) for x in values) or (0,)


def sdk_candidates() -> list[pathlib.Path]:
    values: list[pathlib.Path] = []
    for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os.environ.get(name)
        if value:
            values.append(pathlib.Path(value).expanduser())
    values += [
        pathlib.Path.home() / "Android" / "Sdk",
        pathlib.Path.home() / "Library" / "Android" / "sdk",
        pathlib.Path(os.environ.get("LOCALAPPDATA", str(pathlib.Path.home() / "AppData" / "Local"))) / "Android" / "Sdk",
        pathlib.Path.home() / ".local" / "share" / "android-sdk",
        pathlib.Path("/opt/android-sdk"),
        pathlib.Path("/usr/lib/android-sdk"),
    ]
    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for value in values:
        resolved = value.expanduser().resolve()
        key = str(resolved)
        if key not in seen:
            unique.append(resolved)
            seen.add(key)
    return unique


def find_sdk() -> pathlib.Path:
    for sdk in sdk_candidates():
        if sdk.is_dir() and (sdk / "build-tools").is_dir():
            return sdk
    raise RuntimeError(
        "Android SDK not found. Set ANDROID_SDK_ROOT or install Android SDK build-tools."
    )


def tool_path(build: pathlib.Path, name: str) -> pathlib.Path:
    suffix = (".bat" if name == "apksigner" else ".exe") if os.name == "nt" else ""
    return build / (name + suffix)


def apksigner_command(build: pathlib.Path, java: pathlib.Path) -> list[pathlib.Path | str]:
    if os.name == "nt":
        return [java, "-jar", build / "lib" / "apksigner.jar"]
    return [tool_path(build, "apksigner")]


def find_build_tools(sdk: pathlib.Path) -> pathlib.Path:
    candidates = sorted(
        [p for p in (sdk / "build-tools").iterdir() if p.is_dir()],
        key=version_key,
        reverse=True,
    )
    for build in candidates:
        if all(tool_path(build, name).is_file() for name in ("aapt", "zipalign", "apksigner")):
            return build
    raise RuntimeError(
        f"No Android build-tools directory under {sdk / 'build-tools'} contains aapt, zipalign and apksigner."
    )


def find_java() -> pathlib.Path:
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = pathlib.Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return candidate
    value = shutil.which("java")
    if value:
        return pathlib.Path(value)
    raise RuntimeError("Java is required. Install a JDK (Java 21 or newer).")


def ensure_ovrport(root: pathlib.Path) -> pathlib.Path:
    folder = root / "ovrport-cli"
    jar = folder / "overportcli-1.2.3-all.jar"
    if jar.is_file():
        return jar

    archive = root / "downloads" / "ovrport-cli-1.2.3.zip"
    if not archive.is_file() or sha256(archive) != OVRPORT_ZIP_SHA256:
        download(OVRPORT_ZIP_URL, archive, OVRPORT_ZIP_SHA256)

    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(folder)

    matches = list(folder.rglob("overportcli-1.2.3-all.jar"))
    if not matches:
        raise RuntimeError("Official OVR Port CLI archive did not contain overportcli-1.2.3-all.jar")
    if matches[0] != jar:
        shutil.copy2(matches[0], jar)
    return jar


def ensure_adapter(root: pathlib.Path) -> pathlib.Path:
    output = root / "frame-adapter" / "libopenxr_loader_generic.so"

    # Hash check also invalidates cached adapters from the retired Quest2Frame
    # build path and older FramePort pins.
    if output.is_file() and sha256(output) == FRAMEPORT_ADAPTER_SHA256:
        return output
    output.unlink(missing_ok=True)
    download(FRAMEPORT_ADAPTER_URL, output, FRAMEPORT_ADAPTER_SHA256)
    return output


def ensure_frameport_artifact(root: pathlib.Path, name: str) -> pathlib.Path:
    expected = FRAMEPORT_ARTIFACTS[name]
    output = root / "frame-adapter" / name
    if output.is_file() and sha256(output) == expected:
        return output
    output.unlink(missing_ok=True)
    url = (
        "https://raw.githubusercontent.com/spoopyghosty0/frameport/"
        + FRAMEPORT_COMMIT
        + "/artifacts/arm64-v8a/"
        + name
    )
    download(url, output, expected)
    return output


# --- FramePort game recipes (catalog by spoopyghosty0, GPLv3) ---------------
#
# Recipes from https://github.com/spoopyghosty0/frameport carry, per game,
# the FrameBridge adapter settings that fix known engine bugs and an
# alternate OVRPort patch set. The catalog is distilled into
# frameport-recipes.json next to this script (scripts/distill-frameport-recipes.py).

# Settings our UI owns; a recipe never overrides the user's explicit choice.
RECIPE_USER_OWNED_SETTINGS = ("scale", "controller_fix", "foveation_fix", "refresh_rate")
# Recipe settings that only take effect with a FramePort shim library in the APK.
XRSHIM_SETTINGS = ("controller_models", "haptic_fix", "surface_native")
VK_SHIM_SETTINGS = (
    "vk_shader_fix",
    "vk_spec_fixes",
    "vk_hide_fdm",
    "vk_query_slots",
    "vk_validation",
    "vk_shader_dump",
)
ZINK_LAYER_SETTINGS = ("zink_shader_fix", "zink_shader_dump")
GL_SHIM_SETTINGS = ("gl_int_attribs", "gl_highp_markers", "gl_hide_multiview", "gl_hide_msrtt")

# OVRPort's recommended set (mirrors its own sources); recipes append extras.
DEFAULT_OVRPORT_PATCHES = (
    "patch_copy_libraries",
    "patch_copy_ovrplugin_vrapi",
    "patch_replace_icon_label",
    "patch_fix_min_android_sdk",
    "patch_generate_config",
    "patch_remove_localized_names",
    "patch_clean_up_frida",
    "patch_oculus_unity",
    "patch_oculus_unreal",
    "patch_vr_metadata",
    "patch_launcher_entry",
    "patch_remove_uses_library",
    "patch_fix_unreal_crash",
    "patch_meta_xr_audio",
    "patch_mark_as_debuggable",
    "patch_mark_allow_backup",
)


# Dead payload files from cracked/repacked releases: their runtime bootstrap
# is stripped by OVRPort's patch_clean_up_frida, so nothing loads them — but
# games scan their own APK for them (e.g. Nomad's StoreBase.Validate looks for
# a lib/*.so entry containing "frda") and flag the install as pirated.
CRACK_PAYLOAD_LIBS = frozenset({"libfrda.so", "libscript.so", "libfrda.config.so"})


def load_recipes(path: pathlib.Path | None = None) -> dict:
    if path is None:
        path = pathlib.Path(__file__).resolve().parent / "frameport-recipes.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"FramePort recipes unavailable at {path}")
        return {}
    return data.get("recipes") if isinstance(data, dict) else {}


def recipe_for(package: str, recipes: dict | None = None) -> dict | None:
    entry = (recipes if recipes is not None else load_recipes()).get(package)
    return entry if isinstance(entry, dict) else None


def recipe_adapter_settings(recipe: dict) -> dict[str, str]:
    """Recipe settings the adapter/shims act on; the user's own knobs stay."""
    adapter = recipe.get("adapter") or {}
    merged: dict[str, str] = {}
    for key in sorted(adapter):
        if key in RECIPE_USER_OWNED_SETTINGS:
            log(f"Recipe suggests {key}={adapter[key]}; keeping the user setting")
            continue
        merged[key] = str(adapter[key])
    return merged


def recipe_ovrport_patches(recipe: dict) -> list[str] | None:
    alt = recipe.get("alt_overport") or []
    if not alt:
        return None
    patches = list(DEFAULT_OVRPORT_PATCHES)
    for patch in alt:
        if patch not in patches:
            patches.append(patch)
    return patches


def recipe_shims(recipe: dict) -> dict[str, bool]:
    keys = set((recipe.get("adapter") or {}).keys())
    return {
        "xrshim": any(k in keys for k in XRSHIM_SETTINGS),
        "vk": any(k in keys for k in VK_SHIM_SETTINGS),
        "zink": any(k in keys for k in ZINK_LAYER_SETTINGS),
        "gl": any(k in keys for k in GL_SHIM_SETTINGS),
    }


# --- ELF surgery for the FramePort shims ----------------------------------
#
# elf_add_needed and the in-place string replace are from FramePort by
# spoopyghosty0 (https://github.com/spoopyghosty0/frameport, GPLv3), with the
# section lookup rewritten without pyelftools. Being first in DT_NEEDED
# matters for symbol interposition.

import struct

ELF_MAGIC = b"\x7fELF"


def elf_is_elf(data: bytes) -> bool:
    return data[:4] == ELF_MAGIC


def _elf_header(data: bytes):
    if not elf_is_elf(data) or data[5] != 1:  # little-endian
        raise ValueError("not a little-endian ELF")
    is64 = data[4] == 2
    if is64:
        eh = struct.unpack_from("<16sHHIQQQIHHHHHH", data, 0)
    else:
        eh = struct.unpack_from("<16sHHIIIIIHHHHHH", data, 0)
    return is64, eh


def elf_needed(data: bytes) -> list[str]:
    """DT_NEEDED sonames, from the program headers (no section table needed)."""
    is64, eh = _elf_header(data)
    _, phoff, _, _, phentsize, phnum = eh[4], eh[5], eh[6], eh[7], eh[9], eh[10]
    ph_fmt = "<IIQQQQQQ" if is64 else "<IIIIIIII"
    loads = []
    dyn = None
    for i in range(phnum):
        vals = struct.unpack_from(ph_fmt, data, phoff + i * phentsize)
        if is64:
            p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = vals
        else:
            p_type, p_offset, p_vaddr, _, p_filesz, _, _, _ = vals
        if p_type == 1:  # PT_LOAD
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == 2 and dyn is None:  # PT_DYNAMIC
            dyn = (p_offset, p_filesz)
    if dyn is None:
        return []
    dyn_fmt = "<qQ" if is64 else "<iI"
    esz = struct.calcsize(dyn_fmt)
    entries = []
    for k in range(dyn[1] // esz):
        tag, val = struct.unpack_from(dyn_fmt, data, dyn[0] + k * esz)
        entries.append((tag, val))
        if tag == 0:
            break
    tags = dict(entries)
    strtab_va, strsz = tags.get(5), tags.get(10)  # DT_STRTAB, DT_STRSZ
    if strtab_va is None or strsz is None:
        return []
    off = next((strtab_va - v + o for v, o, sz in loads if v <= strtab_va < v + sz), None)
    if off is None:
        return []
    names = []
    for tag, val in entries:
        if tag == 1 and 0 <= val < strsz:  # DT_NEEDED
            end = data.index(b"\0", off + val)
            names.append(data[off + val : end].decode("utf-8", "replace"))
    return names


def _rodata_bounds(data: bytes) -> tuple[int, int] | None:
    """(offset, size) of .rodata from the section header table."""
    is64, eh = _elf_header(data)
    shoff, shentsize, shnum, shstrndx = eh[6], eh[11], eh[12], eh[13]
    if not shoff or not shnum or shstrndx >= shnum:
        return None
    sh_fmt = "<IIQQQQIIQQ" if is64 else "<IIIIIIIIII"
    names_off = struct.unpack_from(sh_fmt, data, shoff + shstrndx * shentsize)[4]  # shstrtab sh_offset

    def section(i: int):
        return struct.unpack_from(sh_fmt, data, shoff + i * shentsize)

    def name(header) -> str:
        start = names_off + header[0]
        end = data.index(b"\0", start)
        return data[start:end].decode("utf-8", "replace")

    for i in range(shnum):
        header = section(i)
        if name(header) == ".rodata":
            return header[4], header[4] + header[5]  # (start, end) from sh_offset + sh_size
    return None


def elf_replace_rodata_string(data: bytes, old: str, new: str) -> tuple[bytes, int]:
    """Replace NUL-terminated `old` with `new` (not longer; NUL-padded) in .rodata only."""
    if len(new) > len(old):
        raise ValueError("replacement must not be longer")
    bounds = _rodata_bounds(data)
    if bounds is None:
        return data, 0
    start, end = bounds
    needle, repl = old.encode() + b"\0", new.encode().ljust(len(old) + 1, b"\0")
    buf, count, i = bytearray(data), 0, start
    while (i := buf.find(needle, i, end)) != -1:
        if i == start or buf[i - 1] == 0:  # a whole string, not the tail of a longer one
            buf[i : i + len(needle)] = repl
            count += 1
        i += len(needle)
    return bytes(buf), count


def elf_add_needed(data: bytes, library: str) -> bytes:
    """Add a DT_NEEDED entry in front of the existing ones (patchelf --add-needed).

    Nothing already in the file moves: the extended .dynstr and a rebuilt
    .dynamic are appended in a new PT_LOAD (page aligned to 16 KiB), which
    reuses the PT_NOTE program header (bionic ignores notes in shared
    libraries). PT_DYNAMIC, DT_STRTAB/DT_STRSZ and the section headers are
    re-pointed. Handles ELF32 and ELF64 little-endian.
    """
    if library in elf_needed(data):
        return data
    is64, eh = _elf_header(data)
    buf = bytearray(data)
    phoff, shoff, phentsize, phnum, shentsize, shnum = eh[5], eh[6], eh[9], eh[10], eh[11], eh[12]
    ph_fmt, dyn_fmt, sh_fmt = ("<IIQQQQQQ", "<qQ", "<IIQQQQIIQQ") if is64 else ("<IIIIIIII", "<iI", "<IIIIIIIIII")
    PT_LOAD, PT_DYNAMIC, PT_NOTE = 1, 2, 4
    DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ = 0, 1, 5, 10
    PAGE = 16384

    def ph(i):
        vals = list(struct.unpack_from(ph_fmt, buf, phoff + i * phentsize))
        if is64:
            return {"type": vals[0], "flags": vals[1], "offset": vals[2], "vaddr": vals[3], "filesz": vals[5],
                    "memsz": vals[6], "align": vals[7]}
        return {"type": vals[0], "offset": vals[1], "vaddr": vals[2], "filesz": vals[4], "memsz": vals[5],
                "flags": vals[6], "align": vals[7]}

    def write_ph(i, p):
        if is64:
            vals = (p["type"], p["flags"], p["offset"], p["vaddr"], p["vaddr"], p["filesz"], p["memsz"], p["align"])
        else:
            vals = (p["type"], p["offset"], p["vaddr"], p["vaddr"], p["filesz"], p["memsz"], p["flags"], p["align"])
        struct.pack_into(ph_fmt, buf, phoff + i * phentsize, *vals)

    phs = [ph(i) for i in range(phnum)]
    dyn_i = next((i for i, p in enumerate(phs) if p["type"] == PT_DYNAMIC), None)
    note_i = next((i for i, p in enumerate(phs) if p["type"] == PT_NOTE), None)
    if dyn_i is None:
        raise ValueError("no PT_DYNAMIC")
    loads = [p for p in phs if p["type"] == PT_LOAD]
    segs = [(p["vaddr"], p["offset"], p["filesz"]) for p in loads]

    def va2off(va):
        off = next((va - v + o for v, o, sz in segs if v <= va < v + sz), None)
        if off is None:
            raise ValueError(f"vaddr {va:#x} not in a loaded segment")
        return off

    dyn = phs[dyn_i]
    esz = struct.calcsize(dyn_fmt)
    entries = []
    for k in range(dyn["filesz"] // esz):
        tag, val = struct.unpack_from(dyn_fmt, buf, dyn["offset"] + k * esz)
        entries.append([tag, val])
        if tag == DT_NULL:
            break
    tags = {t: v for t, v in entries}
    strtab_va, strsz = tags[DT_STRTAB], tags[DT_STRSZ]
    old_str = bytes(buf[va2off(strtab_va) : va2off(strtab_va) + strsz])
    new_str = old_str + library.encode() + b"\0"
    name_off = len(old_str)

    body_entries = [e for e in entries if e[0] != DT_NULL]
    first_needed = next((k for k, e in enumerate(body_entries) if e[0] == DT_NEEDED), 0)
    body_entries.insert(first_needed, [DT_NEEDED, name_off])
    body_entries += [[DT_NULL, 0]] * 2

    file_end = (len(buf) + PAGE - 1) // PAGE * PAGE
    va_end = max(p["vaddr"] + p["memsz"] for p in loads)
    va_start = (va_end + PAGE - 1) // PAGE * PAGE
    PT_PHDR = 6
    relocate = note_i is None  # no spare header: move the program header table into the new segment
    new_phnum = phnum + (2 if relocate and not any(p["type"] == PT_PHDR for p in phs) else 1 if relocate else 0)
    phdr_bytes = new_phnum * phentsize if relocate else 0
    str_off = (phdr_bytes + 15) // 16 * 16
    dyn_off = (str_off + len(new_str) + 15) // 16 * 16
    for e in body_entries:
        if e[0] == DT_STRTAB:
            e[1] = va_start + str_off
        elif e[0] == DT_STRSZ:
            e[1] = len(new_str)
    dyn_bytes = b"".join(struct.pack(dyn_fmt, t, v) for t, v in body_entries)
    blob_len = dyn_off + len(dyn_bytes)
    new_load = {"type": PT_LOAD, "flags": 6, "offset": file_end, "vaddr": va_start, "filesz": blob_len,
                "memsz": blob_len, "align": PAGE}  # PF_R|PF_W
    new_dyn = {**dyn, "offset": file_end + dyn_off, "vaddr": va_start + dyn_off, "filesz": len(dyn_bytes),
               "memsz": len(dyn_bytes)}

    table = [dict(p) for p in phs]
    table[dyn_i] = new_dyn
    if relocate:
        phdr_entry = {"type": PT_PHDR, "flags": 4, "offset": file_end, "vaddr": va_start, "filesz": phdr_bytes,
                      "memsz": phdr_bytes, "align": 8}
        existing = next((i for i, p in enumerate(table) if p["type"] == PT_PHDR), None)
        if existing is not None:
            table[existing] = phdr_entry
        else:
            table.insert(0, phdr_entry)  # PT_PHDR must precede the loadable segments
        table.append(new_load)
    else:
        table[note_i] = new_load
    # PT_LOAD entries must stay sorted by vaddr (bionic); the new one has the highest vaddr.
    load_slots = [i for i, p in enumerate(table) if p["type"] == PT_LOAD]
    for i, p in zip(load_slots, sorted((table[i] for i in load_slots), key=lambda p: p["vaddr"])):
        table[i] = p

    buf += b"\0" * (file_end - len(buf))
    buf += b"\0" * blob_len
    buf[file_end + str_off : file_end + str_off + len(new_str)] = new_str
    buf[file_end + dyn_off : file_end + dyn_off + len(dyn_bytes)] = dyn_bytes
    if relocate:
        phoff = file_end
        if is64:
            struct.pack_into("<Q", buf, 32, phoff)
            struct.pack_into("<H", buf, 56, new_phnum)
        else:
            struct.pack_into("<I", buf, 28, phoff)
            struct.pack_into("<H", buf, 44, new_phnum)
        phnum = new_phnum
    for i, p in enumerate(table):
        write_ph(i, p)

    # section headers: point .dynamic / .dynstr at the new copies (for tools; the loader uses program headers)
    if shoff and shnum:
        shstrndx = eh[13]

        def sh(i):
            return list(struct.unpack_from(sh_fmt, buf, shoff + i * shentsize))

        names_hdr = sh(shstrndx)
        names_off = names_hdr[4]

        def sec_name(h):
            end = buf.index(b"\0", names_off + h[0])
            return bytes(buf[names_off + h[0] : end]).decode()

        for i in range(shnum):
            h = sh(i)
            n = sec_name(h)
            if n == ".dynstr":
                h[3], h[4], h[5] = va_start + str_off, file_end + str_off, len(new_str)
            elif n == ".dynamic":
                h[3], h[4], h[5] = va_start + dyn_off, file_end + dyn_off, len(dyn_bytes)
            else:
                continue
            struct.pack_into(sh_fmt, buf, shoff + i * shentsize, *h)
    result = bytes(buf)
    got = elf_needed(result)
    if not got or got[0] != library:
        raise RuntimeError(f"add_needed({library}) produced NEEDED={got}")
    return result


# OVRPort's dispatcher aborts on swapchains larger than 4096 px; raise that
# guard so the runtime decides (FramePort default patch, by spoopyghosty0).
SWAPCHAIN_FUNCS = ("xrCreateSwapchain", "xrCreateSwapchainAndroidSurfaceKHR")
CMP_4096 = 0x7140041F  # cmp wN, #1, lsl #12   (subs wzr, wN, #0x1000)
CMP_MASK = 0xFFFFFC1F  # everything but Rn
SWAPCHAIN_LIMIT_UNITS = 4  # 4 << 12 = 16384; the Frame's runtime reports 8192 and rejects larger itself


def _elf64_sections(data: bytes) -> dict[str, tuple[int, int, int]]:
    """name -> (sh_offset, sh_size, sh_entsize) from the section header table."""
    is64, eh = _elf_header(data)
    shoff, shentsize, shnum, shstrndx = eh[6], eh[11], eh[12], eh[13]
    if not shoff or not shnum or shstrndx >= shnum:
        return {}
    sh_fmt = "<IIQQQQIIQQ" if is64 else "<IIIIIIIIII"

    def section(i: int):
        return struct.unpack_from(sh_fmt, data, shoff + i * shentsize)

    names_off = section(shstrndx)[4]
    out: dict[str, tuple[int, int, int]] = {}
    for i in range(shnum):
        header = section(i)
        start = names_off + header[0]
        end = data.index(b"\0", start)
        out[data[start:end].decode("utf-8", "replace")] = (header[4], header[5], header[9])
    return out


def raise_swapchain_limit(data: bytes) -> tuple[bytes | None, int]:
    """Rewrite the `cmp wN, #4096` size guards at the start of OVRPort's swapchain entry points."""
    sections = _elf64_sections(data)
    dynsym = sections.get(".dynsym")
    dynstr = sections.get(".dynstr")
    if not dynsym or not dynstr:
        return None, 0
    sym_off, sym_size, sym_entsize = dynsym
    str_off, str_size, _ = dynstr
    sym_entsize = sym_entsize or 24
    # Elf64_Sym: st_name(4) st_info(1) st_other(1) st_shndx(2) st_value(8) st_size(8)
    loads = []
    is64, eh = _elf_header(data)
    phoff, phentsize, phnum = eh[5], eh[9], eh[10]
    ph_fmt = "<IIQQQQQQ" if is64 else "<IIIIIIII"
    for i in range(phnum):
        vals = struct.unpack_from(ph_fmt, data, phoff + i * phentsize)
        if is64:
            p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = vals
        else:
            p_type, p_offset, p_vaddr, _, p_filesz, _, _, _ = vals
        if p_type == 1:  # PT_LOAD
            loads.append((p_vaddr, p_offset, p_filesz))

    def va2off(va: int) -> int | None:
        return next((va - v + o for v, o, sz in loads if v <= va < v + sz), None)

    out, count = bytearray(data), 0
    for k in range(sym_size // sym_entsize):
        st_name, _info, _other, _shndx, st_value, st_size = struct.unpack_from(
            "<IBBHQQ", data, sym_off + k * sym_entsize
        )
        if st_name >= str_size:
            continue
        end = data.index(b"\0", str_off + st_name)
        name = data[str_off + st_name : end].decode("utf-8", "replace")
        if name not in SWAPCHAIN_FUNCS or not st_value:
            continue
        off = va2off(st_value)
        if off is None:
            continue
        for at in range(off, off + min(st_size or 256, 256), 4):  # the guard sits in the prologue
            word = int.from_bytes(out[at : at + 4], "little")
            if word & CMP_MASK == CMP_4096:
                out[at : at + 4] = (
                    (word & ~(0xFFF << 10)) | (SWAPCHAIN_LIMIT_UNITS << 10)
                ).to_bytes(4, "little")
                count += 1
    return (bytes(out), count) if count else (None, 0)

# --- FramePort default patches (vendored from spoopyghosty0/frameport, GPLv3) --
#
# The following patches mirror FramePort's default-on and recipe-driven engine
# fixes: the Android binary XML editor, the no-op stub library generator, the
# Meta XR Audio telemetry patch (with a word-level ARM64 matcher instead of a
# disassembler), the Meta platform compat/stub libraries, the LTW depth shim,
# the VrApi->OpenXR bridge with the GL shim, the avatar stub, and the BlazeRush
# build-specific edits. All by spoopyghosty0, https://github.com/spoopyghosty0.

OVR_TO_STRING = re.compile(r"^ovr[A-Z][A-Za-z0-9]*_ToString$")


def elf_dyn_symbols(data: bytes, defined: bool) -> set[str]:
    """.dynsym names: defined symbols, or imported (undefined) ones."""
    sections = _elf64_sections(data)
    dynsym = sections.get(".dynsym")
    dynstr = sections.get(".dynstr")
    if not dynsym or not dynstr:
        return set()
    sym_off, sym_size, sym_entsize = dynsym
    str_off, str_size, _ = dynstr
    sym_entsize = sym_entsize or 24
    out: set[str] = set()
    for k in range(sym_size // sym_entsize):
        st_name, _info, _other, st_shndx, _value, _size = struct.unpack_from(
            "<IBBHQQ", data, sym_off + k * sym_entsize
        )
        if st_name == 0 or (st_shndx != 0) != defined:
            continue
        if st_name >= str_size:
            continue
        end = data.index(b"\0", str_off + st_name)
        out.add(data[str_off + st_name : end].decode("utf-8", "replace"))
    return out


def elf_load_segments_flags(data: bytes) -> list[tuple[int, int, int, int]]:
    """(vaddr, offset, filesz, flags) of every PT_LOAD."""
    is64, eh = _elf_header(data)
    phoff, phentsize, phnum = eh[5], eh[9], eh[10]
    ph_fmt = "<IIQQQQQQ" if is64 else "<IIIIIIII"
    out = []
    for i in range(phnum):
        vals = struct.unpack_from(ph_fmt, data, phoff + i * phentsize)
        if is64:
            p_type, p_flags, p_offset, p_vaddr, _, p_filesz, _, _ = vals
        else:
            p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags, _ = vals
        if p_type == 1:
            out.append((p_vaddr, p_offset, p_filesz, p_flags))
    return out


# ---- no-op stub library generator (stubgen, verbatim) -----------------------

STUB_PAGE = 16384
STUB_EMPTY_STRING_AT = 9  # e_ident padding (bytes 9-15 are zero), mapped with the text segment at vaddr 0


def _stub_elf_hash(name: bytes) -> int:
    h = 0
    for c in name:
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    return h


def build_stub_library(symbols: list[str], soname: str = "libovrstubs.so", abi: str = "arm64-v8a",
                       result: int = 0) -> bytes:
    """Generate a tiny Android shared library whose exported functions all `return 0` (no compiler needed)."""
    is64 = abi == "arm64-v8a"
    symbols = sorted(set(symbols))
    if result and not (is64 and -0x10000 <= result <= 0xFFFF):
        raise ValueError(f"stub result {result} not supported for {abi}")
    if is64:
        machine, ehsize, phentsize, shentsize, symsize, dynsize = 183, 64, 56, 64, 24, 16  # EM_AARCH64
        body = struct.pack("<II", 0xD2800000, 0xD65F03C0)  # mov x0, #0 ; ret
        if result > 0:  # movz w0, #result ; ret
            body = struct.pack("<II", 0x52800000 | result << 5, 0xD65F03C0)
        elif result < 0:  # movn w0, #~result ; ret  (w0 = result)
            body = struct.pack("<II", 0x12800000 | (~result & 0xFFFF) << 5, 0xD65F03C0)
    else:
        machine, ehsize, phentsize, shentsize, symsize, dynsize = 40, 52, 32, 40, 16, 8  # EM_ARM
        body = struct.pack("<II", 0xE3A00000, 0xE12FFF1E)  # mov r0, #0 ; bx lr

    # ---- string tables
    dynstr = bytearray(b"\0")

    def add_str(s: str) -> int:
        off = len(dynstr)
        dynstr.extend(s.encode() + b"\0")
        return off

    soname_off = add_str(soname)
    name_offs = [add_str(s) for s in symbols]
    shstr = bytearray(b"\0")
    sec_names = {}
    for n in (".dynsym", ".dynstr", ".hash", ".text", ".dynamic", ".shstrtab"):
        sec_names[n] = len(shstr)
        shstr.extend(n.encode() + b"\0")

    nsyms = len(symbols) + 1
    phnum = 4  # LOAD (R+X), LOAD (RW), DYNAMIC, GNU_STACK
    off = ehsize + phnum * phentsize

    def align(x, a):
        return (x + a - 1) // a * a

    dynsym_off = align(off, 8)
    dynstr_off = dynsym_off + nsyms * symsize
    hash_off = align(dynstr_off + len(dynstr), 4)
    nbucket = max(1, len(symbols) // 2 + 1)
    hash_size = 4 * (2 + nbucket + nsyms)
    text_off = align(hash_off + hash_size, 16)
    text_size = 8 * len(symbols)
    seg1_end = text_off + text_size
    dyn_off = align(seg1_end, STUB_PAGE)  # own page, RW
    dyn_entries = [(5, dynstr_off), (6, dynsym_off), (10, len(dynstr)), (11, symsize), (4, hash_off),
                   (14, soname_off), (0, 0)]
    dyn_bytes = b"".join(struct.pack("<qQ" if is64 else "<iI", t, v) for t, v in dyn_entries)
    shstr_off = dyn_off + len(dyn_bytes)
    sh_off = align(shstr_off + len(shstr), 8)

    # ---- symbols
    SHN_TEXT = 4
    syms = bytearray(b"\0" * symsize)
    for i, name_off in enumerate(name_offs):
        value = text_off + 8 * i
        info = (1 << 4) | 2  # STB_GLOBAL, STT_FUNC
        if is64:
            syms += struct.pack("<IBBHQQ", name_off, info, 0, SHN_TEXT, value, 8)
        else:
            syms += struct.pack("<IIIBBH", name_off, value, 8, info, 0, SHN_TEXT)
    buckets, chains = [0] * nbucket, [0] * nsyms
    for idx in range(1, nsyms):
        b = _stub_elf_hash(symbols[idx - 1].encode()) % nbucket
        chains[idx] = buckets[b]
        buckets[b] = idx
    hash_bytes = struct.pack(f"<II{nbucket}I{nsyms}I", nbucket, nsyms, *buckets, *chains)

    # ---- headers
    PF_R, PF_W, PF_X = 4, 2, 1
    phdrs = [
        (1, PF_R | PF_X, 0, 0, seg1_end, seg1_end, STUB_PAGE),
        (1, PF_R | PF_W, dyn_off, dyn_off, len(dyn_bytes), len(dyn_bytes), STUB_PAGE),
        (2, PF_R | PF_W, dyn_off, dyn_off, len(dyn_bytes), len(dyn_bytes), 8),
        (0x6474E551, PF_R | PF_W, 0, 0, 0, 0, 16),  # PT_GNU_STACK
    ]
    ph_bytes = b""
    for typ, flags, o, va, fsz, msz, al in phdrs:
        if is64:
            ph_bytes += struct.pack("<IIQQQQQQ", typ, flags, o, va, va, fsz, msz, al)
        else:
            ph_bytes += struct.pack("<IIIIIIII", typ, o, va, va, fsz, msz, flags, al)
    SHT_PROGBITS, SHT_STRTAB, SHT_HASH, SHT_DYNAMIC, SHT_DYNSYM = 1, 3, 5, 6, 11
    SHF_WRITE, SHF_ALLOC, SHF_EXEC = 1, 2, 4
    sections = [
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (sec_names[".dynsym"], SHT_DYNSYM, SHF_ALLOC, dynsym_off, dynsym_off, nsyms * symsize, 2, 1, 8, symsize),
        (sec_names[".dynstr"], SHT_STRTAB, SHF_ALLOC, dynstr_off, dynstr_off, len(dynstr), 0, 0, 1, 0),
        (sec_names[".hash"], SHT_HASH, SHF_ALLOC, hash_off, hash_off, hash_size, 1, 0, 4, 4),
        (sec_names[".text"], SHT_PROGBITS, SHF_ALLOC | SHF_EXEC, text_off, text_off, text_size, 0, 0, 16, 0),
        (sec_names[".dynamic"], SHT_DYNAMIC, SHF_ALLOC | SHF_WRITE, dyn_off, dyn_off, len(dyn_bytes), 2, 0, 8, dynsize),
        (sec_names[".shstrtab"], SHT_STRTAB, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0),
    ]
    sh_bytes = b""
    for name, typ, flags, addr, o, size, link, info, al, entsize in sections:
        if is64:
            sh_bytes += struct.pack("<IIQQQQIIQQ", name, typ, flags, addr, o, size, link, info, al, entsize)
        else:
            sh_bytes += struct.pack("<IIIIIIIIII", name, typ, flags, addr, o, size, link, info, al, entsize)
    ident = b"\x7fELF" + bytes([2 if is64 else 1, 1, 1, 0]) + b"\0" * 8
    if is64:
        eh = ident + struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, ehsize, sh_off, 0, ehsize, phentsize, phnum,
                                 shentsize, len(sections), 6)
    else:
        eh = ident + struct.pack("<HHIIIIIHHHHHH", 3, machine, 1, 0, ehsize, sh_off, 0x05000000, ehsize, phentsize,
                                 phnum, shentsize, len(sections), 6)  # EF_ARM_EABI_VER5

    out = bytearray(sh_off + len(sh_bytes))
    out[0:len(eh)] = eh
    out[ehsize:ehsize + len(ph_bytes)] = ph_bytes
    out[dynsym_off:dynsym_off + len(syms)] = syms
    out[dynstr_off:dynstr_off + len(dynstr)] = dynstr
    out[hash_off:hash_off + len(hash_bytes)] = hash_bytes
    text = bytearray(body * len(symbols))
    if is64:  # *_ToString answers "" instead of NULL (a caller may build a string from it)
        for i, name in enumerate(symbols):
            if name.endswith("_ToString"):
                pc = text_off + 8 * i
                imm = (STUB_EMPTY_STRING_AT - pc) & 0x1FFFFF  # 21-bit signed offset
                text[8 * i:8 * i + 8] = struct.pack("<II", 0x10000000 | (imm & 3) << 29 | (imm >> 2) << 5, 0xD65F03C0)
    out[text_off:text_off + text_size] = text
    out[dyn_off:dyn_off + len(dyn_bytes)] = dyn_bytes
    out[shstr_off:shstr_off + len(shstr)] = shstr
    out[sh_off:] = sh_bytes
    return bytes(out)


# ---- Android binary XML editor (axml, verbatim subset) -----------------------

RES_XML = 0x0003
RES_STRING_POOL = 0x0001
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
TYPE_STRING = 0x03
TYPE_INT_BOOLEAN = 0x12
AXML_LAUNCHER = "android.intent.category.LAUNCHER"
AXML_INFO = "android.intent.category.INFO"


class AxmlAttr:
    def __init__(self, offset: int, ns: int, name: int, raw: int, dtype: int, value: int):
        self.offset = offset
        self.ns = ns
        self.name = name
        self.raw = raw
        self.dtype = dtype
        self.value = value


class AxmlElement:
    def __init__(self, offset: int, size: int, name: str, attrs: list):
        self.offset = offset
        self.size = size
        self.name = name
        self.attrs = attrs


class Axml:
    def __init__(self, data: bytes):
        self.data = bytearray(data)
        typ, hsz, _ = struct.unpack_from("<HHI", self.data, 0)
        if typ != RES_XML:
            raise ValueError("not a binary XML file")
        self.pool_off = hsz
        ptype, phsz, psize, self.count, self.styles, self.flags, self.sstart, self.ystart = struct.unpack_from(
            "<HHIIIIII", self.data, self.pool_off
        )
        if ptype != RES_STRING_POOL:
            raise ValueError("string pool not found")
        self.pool_hsz, self.pool_size = phsz, psize
        self.utf8 = bool(self.flags & 0x100)
        base = self.pool_off + phsz
        self.offsets = list(struct.unpack_from(f"<{self.count}I", self.data, base))
        self.style_offsets = list(struct.unpack_from(f"<{self.styles}I", self.data, base + 4 * self.count))
        self._strings: list[str] | None = None

    # ------------------------------------------------------------------ strings
    def string(self, i: int) -> str:
        p = self.pool_off + self.sstart + self.offsets[i]
        d = self.data
        if self.utf8:
            n = d[p]
            p += 2 if n & 0x80 else 1  # char count
            n = d[p]
            nb = ((n & 0x7F) << 8 | d[p + 1]) if n & 0x80 else n
            p += 2 if n & 0x80 else 1
            return bytes(d[p : p + nb]).decode("utf-8", "replace")
        n = struct.unpack_from("<H", d, p)[0]
        p += 2
        if n & 0x8000:
            n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", d, p)[0]
            p += 2
        return bytes(d[p : p + 2 * n]).decode("utf-16-le", "replace")

    def strings(self) -> list[str]:
        if self._strings is None:
            self._strings = [self.string(i) for i in range(self.count)]
        return self._strings

    def append_string(self, s: str) -> int:
        """Append s to the pool; returns its index. Rebuilds the pool chunk and the file size."""
        if self.utf8:
            b = s.encode("utf-8")
            if len(s) >= 0x80 or len(b) >= 0x80:
                raise ValueError("long UTF-8 strings are not supported")
            enc = bytes([len(s), len(b)]) + b + b"\0"
        else:
            enc = struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0"
        d, po = self.data, self.pool_off
        str_end = po + (self.ystart if self.styles else self.pool_size)
        str_data = bytes(d[po + self.sstart : str_end])
        new_off = len(str_data)
        str_data += enc
        str_data += b"\0" * (-len(str_data) % 4)
        style_data = bytes(d[po + self.ystart : po + self.pool_size]) if self.styles else b""
        offsets = self.offsets + [new_off]
        sstart = self.pool_hsz + 4 * len(offsets) + 4 * self.styles
        ystart = sstart + len(str_data) if self.styles else 0
        body = (
            struct.pack(f"<{len(offsets)}I", *offsets)
            + struct.pack(f"<{self.styles}I", *self.style_offsets)
            + str_data
            + style_data
        )
        header = struct.pack(
            "<HHIIIIII", RES_STRING_POOL, self.pool_hsz, self.pool_hsz + len(body), len(offsets), self.styles,
            self.flags, sstart, ystart,
        )
        header += bytes(d[po + 28 : po + self.pool_hsz])  # any extra header bytes
        new = bytes(d[:po]) + header + body + bytes(d[po + self.pool_size :])
        self.__init__(new)
        struct.pack_into("<I", self.data, 4, len(self.data))
        return len(offsets) - 1

    def index(self, s: str) -> int | None:
        try:
            return self.strings().index(s)
        except ValueError:
            return None

    # ------------------------------------------------------------------ elements
    def elements(self):
        names = self.strings()
        pos = struct.unpack_from("<H", self.data, 2)[0]
        while pos < len(self.data):
            typ, hsz, size = struct.unpack_from("<HHI", self.data, pos)
            if size == 0:
                break
            if typ == RES_XML_START_ELEMENT:
                ext = pos + hsz
                _, name, astart, asize, acount = struct.unpack_from("<IIHHH", self.data, ext)
                attrs = []
                for a in range(acount):
                    at = ext + astart + a * asize
                    ns, aname, raw = struct.unpack_from("<III", self.data, at)
                    dtype = self.data[at + 15]
                    value = struct.unpack_from("<I", self.data, at + 16)[0]
                    attrs.append(AxmlAttr(at, ns, aname, raw, dtype, value))
                yield AxmlElement(pos, size, names[name] if name < len(names) else "", attrs)
            pos += size

    def attr_str(self, el: AxmlElement, attr: str) -> str | None:
        names = self.strings()
        for a in el.attrs:
            if a.name < len(names) and names[a.name] == attr:
                if a.raw < len(names):
                    return names[a.raw]
                if a.dtype == TYPE_STRING and a.value < len(names):
                    return names[a.value]
        return None

    # ------------------------------------------------------------------ edits
    def retarget(self, element: str, old_index: int, new_index: int) -> int:
        """In every <element>, point string-valued attributes that reference old_index at new_index."""
        hits = 0
        for el in self.elements():
            if el.name != element:
                continue
            for a in el.attrs:
                if a.raw == old_index or (a.dtype == TYPE_STRING and a.value == old_index):
                    struct.pack_into("<I", self.data, a.offset + 8, new_index)
                    if a.dtype == TYPE_STRING:
                        struct.pack_into("<I", self.data, a.offset + 16, new_index)
                    hits += 1
        return hits

    def set_bool(self, element: str, attr: str, value: bool) -> int:
        names = self.strings()
        hits = 0
        for el in self.elements():
            if el.name != element:
                continue
            for a in el.attrs:
                if a.name < len(names) and names[a.name] == attr and a.dtype == TYPE_INT_BOOLEAN:
                    struct.pack_into("<I", self.data, a.offset + 16, 0xFFFFFFFF if value else 0)
                    hits += 1
        return hits

    def bytes(self) -> bytes:
        return bytes(self.data)


def axml_used_and_declared_permissions(manifest: bytes) -> tuple[list[str], set[str]]:
    x = Axml(manifest)
    used, declared = [], set()
    for el in x.elements():
        if el.name in ("uses-permission", "permission"):
            name = x.attr_str(el, "name")
            if name:
                (used.append if el.name == "uses-permission" else declared.add)(name)
    return used, declared


def axml_undeclared_meta_permissions(manifest: bytes) -> list[str]:
    used, declared = axml_used_and_declared_permissions(manifest)
    return [p for p in used if p.startswith(("com.oculus.permission.", "horizonos.permission.")) and p not in declared]


def axml_define_meta_permissions(manifest: bytes):
    """Declare Meta-only permissions (com.oculus.*, horizonos.*) the app uses. A permission the app declares
    itself with the default 'normal' protection level is granted at install, so checks like "allow spatial
    data" pass (mixed-reality games, for example Demeter)."""
    wanted = axml_undeclared_meta_permissions(manifest)
    x = Axml(manifest)
    if not wanted or x.index("name") is None:
        return None
    android_ns = x.index("http://schemas.android.com/apk/res/android")
    if android_ns is None:
        return None
    perm_el = x.index("permission")
    if perm_el is None:
        perm_el = x.append_string("permission")
    names = x.strings()
    name_attr = names.index("name")
    chunks = b""
    for p in wanted:
        value = names.index(p)
        start = struct.pack("<HHIII", RES_XML_START_ELEMENT, 16, 56, 0, 0xFFFFFFFF)
        start += struct.pack("<IIHHHHHH", 0xFFFFFFFF, perm_el, 20, 20, 1, 0, 0, 0)
        start += struct.pack("<IIIHBBI", android_ns, name_attr, value, 8, 0, TYPE_STRING, value)
        end = (
            struct.pack("<HHIII", RES_XML_END_ELEMENT, 16, 24, 0, 0xFFFFFFFF)
            + struct.pack("<II", 0xFFFFFFFF, perm_el)
        )
        chunks += start + end
    manifest_el = next((el for el in x.elements() if el.name == "manifest"), None)
    if manifest_el is None:
        return None
    insert_at = manifest_el.offset + manifest_el.size
    data = x.data[:insert_at] + chunks + x.data[insert_at:]
    struct.pack_into("<I", data, 4, len(data))
    return bytes(data), wanted


def axml_categories(manifest: bytes) -> set[str]:
    x = Axml(manifest)
    return {v for el in x.elements() if el.name == "category" for v in [x.attr_str(el, "name")] if v}


def axml_fix_launcher(manifest: bytes) -> bytes | None:
    """Lepton only launches activities with category LAUNCHER: retarget category INFO to LAUNCHER."""
    x = Axml(manifest)
    names = x.strings()
    if AXML_INFO not in names or AXML_LAUNCHER in names:
        return None
    info = names.index(AXML_INFO)
    launcher = x.append_string(AXML_LAUNCHER)
    return x.bytes() if x.retarget("category", info, launcher) else None


def axml_set_bool_attr(manifest: bytes, element: str, attr: str, value: bool) -> bytes | None:
    x = Axml(manifest)
    return x.bytes() if x.set_bool(element, attr, value) else None


# ---- Meta XR Audio telemetry patch (word-level ARM64 matcher) --------------

TELEMETRY_CLASS = b"com/oculus/os/AnalyticsEvent\0"


def patch_metaxr_telemetry(data: bytes) -> bytes | None:
    """Replace the JNI FindClass(AnalyticsEvent) call in Meta XR Audio with `mov x0, xzr`, so the
    pending ClassNotFoundException doesn't abort ART (pattern from FramePort, matched without a
    disassembler: ldr xN,[xN,#0x30]; blr xN; cbz with an adrp/add pair pointing at the class string)."""
    if TELEMETRY_CLASS not in data or not elf_is_elf(data) or data[4] != 2 or data[5] != 1:
        return None
    segments = elf_load_segments_flags(data)
    exec_segs = [(v, o, sz) for v, o, sz, fl in segments if fl & 1]

    def va2off(va: int) -> int | None:
        return next((va - v + o for v, o, sz, _ in segments if v <= va < v + sz), None)

    out = bytearray(data)
    patched = 0
    for vaddr, off, size in exec_segs:
        for at in range(off, off + size - 8, 4):
            w = int.from_bytes(data[at : at + 4], "little")
            if (w & 0xFFC00000) != 0xF9400000 or ((w >> 10) & 0xFFF) != 6:  # ldr xN, [xM, #0x30]
                continue
            rn = (w >> 5) & 31
            if rn != (w & 31):
                continue
            w2 = int.from_bytes(data[at + 4 : at + 8], "little")
            if (w2 & 0xFFFFFC00) != 0xD63F0000 or ((w2 >> 5) & 31) != rn:  # blr xN
                continue
            w3 = int.from_bytes(data[at + 8 : at + 12], "little")
            if (w3 & 0x7F000000) != 0x34000000:  # cbz
                continue
            page = low = None
            for back in range(at - 4, max(off - 4, at - 4 * 14) - 4, -4):
                wb = int.from_bytes(data[back : back + 4], "little")
                if low is None and (wb & 0xFFC00000) == 0x91000000 and ((wb >> 5) & 31) == 1 and (wb & 31) == 1:
                    low = (wb >> 10) & 0xFFF
                if (wb & 0x9F00001F) == 0x90000001:  # adrp x1, ...
                    immlo = (wb >> 29) & 3
                    immhi = (wb >> 5) & 0x7FFFF
                    imm = (immhi << 2) | immlo
                    if imm & 0x100000:
                        imm -= 0x200000
                    pc = vaddr + (back - off)
                    page = (pc & ~0xFFF) + (imm << 12)
                    break
            if page is None or low is None:
                continue
            so = va2off(page + low)
            if so is None or not data[so:].startswith(TELEMETRY_CLASS):
                continue
            out[at + 4 : at + 8] = (0xAA1F03E0).to_bytes(4, "little")  # mov x0, xzr
            patched += 1
    return bytes(out) if patched else None


# ---- Meta platform compat + stubs ------------------------------------------

def missing_ovr_symbols(lib_bytes: dict) -> set[str]:
    """ovr_* functions the game imports that the platform loader (+compat/stub libs) lacks."""
    loader = lib_bytes.get("libovrplatformloader.so")
    if not loader or not elf_is_elf(loader):
        return set()
    exported = elf_dyn_symbols(loader, True)
    linked = set(elf_needed(loader))
    for extra in ("libovrplatformcompat.so", "libovrstubs.so"):
        if extra in lib_bytes and extra in linked:
            exported |= elf_dyn_symbols(lib_bytes[extra], True)
    wanted = set()
    for name, blob in lib_bytes.items():
        if name.startswith(("libovrplatformloader", "libopenxr_loader", "libframe_settings")) or not elf_is_elf(blob):
            continue
        wanted |= {
            s
            for s in elf_dyn_symbols(blob, False)
            if s.startswith(("ovr_", "ovrMessageType_")) or OVR_TO_STRING.match(s)
        }
    return wanted - exported


# ---- BlazeRush (ru.targem.blazerush 1.0.349, from Klownicle's guide) --------

BLAZERUSH_LIB = "libtargemapp.so"
BLAZERUSH_NOM = 0xD503201F
BLAZERUSH_RET = 0xD65F03C0
BLAZERUSH_OLD_CACHE = b"cache/shader_cache\0"
BLAZERUSH_NEW_CACHE = b"cache/br_shader_v1\0"
BLAZERUSH_BUILDS = (
    # (version, text_sha256, sites, cache_va); sites: (va, original word, replacement, what)
    ("1.0.349", "951bf1d5208ae36603f73990346d164a8523e7d1e60cc3e67d3571c7fc55d5f4",
     ((0x4B2DBC, 0x36000AE0, BLAZERUSH_NOM, "avatar readiness wait (tbz) -> nop"),
      (0x4B2ADC, 0xF0002368, BLAZERUSH_RET, "avatar mesh renderer -> ret")),
     0x13AF70),
)


def _exec_segment(data: bytes, va: int):
    """(vaddr, file offset, size) of the executable PT_LOAD segment holding `va`."""
    for vaddr, offset, size, flags in elf_load_segments_flags(data):
        if flags & 1 and vaddr <= va < vaddr + size:
            return vaddr, offset, size
    return None


def blazerush_sites(data: bytes):
    """The edits for a supported build, or None (another build / damaged library). Fingerprint-checked."""
    if not elf_is_elf(data) or data[4] != 2 or data[5] != 1:
        return None
    if struct.unpack_from("<H", data, 18)[0] != 183:  # EM_AARCH64
        return None
    segments = elf_load_segments_flags(data)
    for build in BLAZERUSH_BUILDS:
        text = _exec_segment(data, build[2][0][0])
        if not text:
            continue
        t_va, t_off, t_size = text
        code = bytearray(data[t_off : t_off + t_size])
        edits, ok = [], True
        for va, old, new, what in build[2]:
            if not t_va <= va < t_va + t_size - 3:
                ok = False
                break
            at = va - t_va
            word = struct.unpack_from("<I", code, at)[0]
            if word not in (old, new):
                ok = False
                break
            struct.pack_into("<I", code, at, old)  # fingerprint over the code as shipped
            edits.append((t_off + at, old, new, what))
        if not ok or hashlib.sha256(bytes(code)).hexdigest() != build[1]:
            continue
        off = next((build[3] - v + o for v, o, sz, _ in segments if v <= build[3] < v + sz), None)
        if off is None:
            continue
        here = data[off : off + len(BLAZERUSH_OLD_CACHE)]
        old_n, new_n = data.count(BLAZERUSH_OLD_CACHE), data.count(BLAZERUSH_NEW_CACHE)
        if not ((here == BLAZERUSH_OLD_CACHE and old_n == 1 and new_n == 0)
                or (here == BLAZERUSH_NEW_CACHE and old_n == 0 and new_n == 1)):
            continue
        return {"edits": edits, "cache_offset": off}
    return None


def patch_blazerush(data: bytes):
    """(edited library, notes) for a supported build; unchanged and None otherwise."""
    site = blazerush_sites(data)
    if not site:
        return data, []
    buf = bytearray(data)
    notes = []
    for at, old, new, what in site["edits"]:
        buf[at : at + 4] = new.to_bytes(4, "little")
        notes.append(what)
    buf[site["cache_offset"] : site["cache_offset"] + len(BLAZERUSH_NEW_CACHE)] = BLAZERUSH_NEW_CACHE
    notes.append("shader cache namespace")
    return bytes(buf), notes



def resolve_apk(source: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    source = source.expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() != ".apk":
            raise RuntimeError(f"Expected an APK or extracted folder, got {source}")
        return source, source.parent
    if not source.is_dir():
        raise RuntimeError(f"Input does not exist: {source}")

    apks = [p for p in source.rglob("*.apk") if len(p.relative_to(source).parts) <= 3]
    if not apks:
        raise RuntimeError("No APK found in input folder")
    root_apks = [p for p in apks if p.parent == source]
    named = [p for p in apks if p.name.lower() in ("game.apk", "base.apk")]
    preferred = (named or root_apks or apks)
    if len(apks) > 1 and not named and len(root_apks) != 1:
        raise RuntimeError(
            "Multiple APKs found. Split/multi-APK packages are not supported by the Frame converter yet."
        )
    return preferred[0], source


SUPPORTED_REFRESH_RATES = (0, 72, 80, 90, 96, 108, 120, 144)


def normalize_refresh_rate(value: int | float | str) -> int:
    try:
        rate = int(float(value))
    except (TypeError, ValueError):
        raise RuntimeError("Refresh rate must be Default, 72, 80, 90, 96, 108, 120, or 144 Hz")
    if rate not in SUPPORTED_REFRESH_RATES:
        raise RuntimeError("Refresh rate must be Default, 72, 80, 90, 96, 108, 120, or 144 Hz")
    return rate


def settings_bytes(
    scale: float,
    controllers: bool,
    foveation: bool,
    refresh_rate: int = 0,
    extra: dict[str, str] | None = None,
) -> bytes:
    if scale < 50 or scale > 200:
        raise RuntimeError("Resolution scale must be between 50 and 200 percent")
    refresh_rate = normalize_refresh_rate(refresh_rate)
    text = (
        f"scale={scale / 100.0:.3f}\n"
        f"controller_fix={1 if controllers else 0}\n"
        f"foveation_fix={1 if foveation else 0}\n"
        f"refresh_rate={refresh_rate}\n"
    )
    for key in extra or {}:
        text += f"{key}={extra[key]}\n"
    return text.encode("utf-8")


def is_frame_converted(apk: pathlib.Path) -> bool:
    try:
        with zipfile.ZipFile(apk) as zf:
            names = set(zf.namelist())
        prefix = "lib/arm64-v8a/"
        return (
            prefix + "libframe_settings.so" in names
            and prefix + "libopenxr_loader_original.so" in names
            and prefix + "libopenxr_loader_generic.so" in names
        )
    except zipfile.BadZipFile:
        return False


def copy_obbs(source_root: pathlib.Path, destination: pathlib.Path, package: str) -> int:
    # Two release layouts carry game data: classic OBB files named
    # *.<package>.obb anywhere in the release, and a folder named after the
    # package that holds the data files directly (raw Unity bundles, e.g.
    # Blade & Sorcery). Both land in obb/, mirroring the
    # /sdcard/Android/obb/<package> push a Quest install performs.
    files: list[tuple[str, pathlib.Path]] = []
    for folder in source_root.rglob(package):
        if not folder.is_dir() or destination in folder.parents:
            continue
        for file in folder.rglob("*"):
            if file.is_file() and destination not in file.parents:
                files.append((file.relative_to(folder).as_posix(), file))
    for obb in source_root.rglob("*.obb"):
        if obb.is_file() and obb.name.endswith(f".{package}.obb") and destination not in obb.parents:
            files.append((obb.name, obb))
    if not files:
        return 0
    obb_dir = destination / "obb"
    obb_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    for name, source in files:
        digest = sha256(source)
        if name in seen:
            # Nested duplicate releases must contain identical data.
            if seen[name] != digest:
                raise RuntimeError(
                    f"Conflicting OBB files with the same name but different contents: {name}"
                )
            continue
        seen[name] = digest
        target = obb_dir.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return len(seen)


def convert(
    input_path: pathlib.Path,
    output_dir: pathlib.Path,
    *,
    scale: float,
    controllers: bool,
    foveation: bool,
    refresh_rate: int = 0,
) -> dict:
    source_apk, source_root = resolve_apk(input_path)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    game_apk = output_dir / "game.apk"
    if game_apk == source_apk:
        raise RuntimeError("Output APK would overwrite the original source; choose another output directory")

    refresh_rate = normalize_refresh_rate(refresh_rate)

    sdk = find_sdk()
    build = find_build_tools(sdk)
    badging = run([tool_path(build, "aapt"), "dump", "badging", source_apk])
    package_match = re.search(r"package: name='([^']+)'", badging)
    if not package_match:
        raise RuntimeError("Could not determine Android package name with aapt")
    package = package_match.group(1)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package):
        raise RuntimeError(f"Invalid Android package name reported by aapt: {package}")

    version_code_match = re.search(r"versionCode='(\d+)'", badging)
    version_name_match = re.search(r"versionName='([^']*)'", badging)
    label_match = re.search(r"application-label:'([^']*)'", badging)
    version_code = int(version_code_match.group(1)) if version_code_match else 0
    version_name = version_name_match.group(1) if version_name_match else ""
    application_label = label_match.group(1) if label_match else package

    recipe = recipe_for(package)
    recipe_settings: dict[str, str] = {}
    recipe_patches: list[str] | None = None
    if recipe:
        log(f"Applying FramePort recipe for {recipe.get('title') or package} (FramePort by spoopyghosty0)")
        recipe_settings = recipe_adapter_settings(recipe)
        recipe_patches = recipe_ovrport_patches(recipe)

    if is_frame_converted(source_apk):
        if refresh_rate:
            raise RuntimeError(
                "Custom refresh settings require reconversion from the original Quest APK."
            )
        log("APK already contains Frame conversion markers; copying without repatching.")
        if recipe and (recipe_settings or recipe_patches):
            log("Recipe changes need a reconversion from the original Quest APK; installing as-is.")
        shutil.copy2(source_apk, game_apk)
        obb_count = copy_obbs(source_root, output_dir, package)
        result = {
            "ok": True,
            "converted": False,
            "alreadyConverted": True,
            "package": package,
            "versionCode": version_code,
            "versionName": version_name,
            "applicationLabel": application_label,
            "gameApk": str(game_apk),
            "directory": str(output_dir),
            "obbCount": obb_count,
            "sha256": sha256(game_apk),
        }

        (output_dir / "frame-conversion.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    root = tools_root()
    java = find_java()
    jar = ensure_ovrport(root)
    adapter = ensure_adapter(root)

    with zipfile.ZipFile(source_apk) as source_zip:
        names = source_zip.namelist()
        if not any(name.startswith("lib/arm64-v8a/") for name in names):
            raise RuntimeError("APK does not contain ARM64 libraries")

    workspace = root / "ovrport-workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    work = pathlib.Path(tempfile.mkdtemp(prefix="frame-cyberdeck-convert-", dir=output_dir))
    try:
        patch_dir = work / "ovrport"
        patch_dir.mkdir(parents=True)
        log(f"Converting {source_apk.name} with OVR Port runtime {OVRPORT_RUNTIME_VERSION}")
        patch_args: list[str] = []
        if recipe_patches:
            # shortcut: the explicit list pins OVRPort's recommended set; new
            # OVRPort releases may add recommended patches this list misses
            patch_args = [f"--patches={';'.join(recipe_patches)}"]
            log(f"OVRPort patch set: {', '.join(recipe_patches)}")
        run(
            [
                java,
                "-jar",
                jar,
                "patch",
                f"--input={source_apk}",
                f"--output={patch_dir}",
                "--output-name=ovrport.apk",
                f"--workspace={workspace}",
                f"--version={OVRPORT_RUNTIME_VERSION}",
                *patch_args,
            ]
        )

        patched = patch_dir / "ovrport.apk"
        if not patched.is_file():
            matches = list(patch_dir.rglob("ovrport.apk"))
            if not matches:
                raise RuntimeError("OVR Port did not produce ovrport.apk")
            patched = matches[0]

        unsigned = work / "unsigned.apk"
        prefix = "lib/arm64-v8a/"
        loader = prefix + "libopenxr_loader_generic.so"

        # FramePort default + recipe patches (by spoopyghosty0, GPLv3)
        shims = recipe_shims(recipe or {})
        prefix_libs: dict[str, bytes] = {}
        manifest_bytes: bytes | None = None
        with zipfile.ZipFile(patched) as probe:
            for n in probe.namelist():
                if n.startswith(prefix):
                    prefix_libs[pathlib.Path(n).name] = probe.read(n)
                elif n == "AndroidManifest.xml":
                    manifest_bytes = probe.read(n)

        metaxr_libs = [n for n in prefix_libs if n.startswith("libmetaxraudio")]
        if metaxr_libs:
            # CheckJNI rejects the pending telemetry exception; clear android:debuggable
            if manifest_bytes is not None:
                fixed_manifest = axml_set_bool_attr(manifest_bytes, "application", "debuggable", False)
                if fixed_manifest:
                    manifest_bytes = fixed_manifest
                    log("Cleared android:debuggable (Meta XR Audio build)")

        if manifest_bytes is not None:
            fixed_manifest = axml_fix_launcher(manifest_bytes)
            if fixed_manifest:
                manifest_bytes = fixed_manifest
                log("Retargeted category INFO to LAUNCHER (Lepton only launches LAUNCHER)")
            declared = axml_define_meta_permissions(manifest_bytes)
            if declared:
                manifest_bytes = declared[0]
                log(f"Declared {len(declared[1])} Meta permission(s): {', '.join(declared[1])}")

        platform_missing: set[str] = set()
        platform_compat = False
        if "libovrplatformloader.so" in prefix_libs:
            platform_missing = missing_ovr_symbols(prefix_libs)
            platform_compat = "ovrMessageType_ToString" in platform_missing
            if platform_missing:
                log(f"OVR platform: {len(platform_missing)} missing ovr_* function(s) to cover")

        avatar_stub = None
        if "libovravatarloader.so" in prefix_libs and elf_is_elf(prefix_libs["libovravatarloader.so"]):
            exports = sorted(x for x in elf_dyn_symbols(prefix_libs["libovravatarloader.so"], True) if x.startswith("ovr"))
            if exports:
                avatar_stub = build_stub_library(exports, soname="libovravatarloader.so")
                log(f"Stubbed {len(exports)} Meta avatar functions (no Horizon avatar driver on the Frame)")

        ltw = "libltw.so" in prefix_libs  # Minecraft launchers: depth formats

        blazerush = package == "ru.targem.blazerush"

        # Which APK libraries load Vulkan by name: the Vulkan shim applies to every
        # such engine (FramePort default: it drops invalid render-pass pNext pointers
        # that crash Lepton's Fossilize layer), not just vk_* recipes.
        not_engine = ("libopenxr", "libOVR", "libovr", "libvrapi", "libfp_", "libframe", "libVkLayer", "libc++", "libunity")
        vk_targets: list[str] = []
        for name, blob in prefix_libs.items():
            if name in ("libUE4.so", "libUnreal.so") or (
                not name.startswith(not_engine) and b"\0libvulkan.so\0" in blob
            ):
                vk_targets.append(name)
        if vk_targets:
            shims["vk"] = True

        # The recipe's GL flow: the VrApi->OpenXR bridge replaces libvrapi.so, and the
        # GL shim loads ahead of libEGL in every engine library that resolves GL
        # through eglGetProcAddress (Mesa rejects Quest-only GLSL leniency).
        gl_targets: list[str] = []
        if shims["gl"] and "libvrapi.so" in prefix_libs:
            for name, blob in prefix_libs.items():
                if name in ("libvrapi.so", "libOVRPlugin.so", "libglshim.so") or name.startswith("libopenxr_loader"):
                    continue
                if "eglGetProcAddress" in elf_dyn_symbols(blob, False):
                    gl_targets.append(name)
        elif shims["gl"]:
            log("Recipe requests the GL shim but the APK has no libvrapi.so to bridge; GL fixes skipped.")

        stub_bytes: dict[str, bytes] = {}
        if platform_missing:
            stub_bytes["libovrstubs.so"] = build_stub_library(sorted(platform_missing))

        shim_bytes = {
            name: ensure_frameport_artifact(root, name).read_bytes()
            for name, needed in (
                ("libframe_xrshim.so", shims["xrshim"]),
                ("libfp_vk.so", shims["vk"]),
                ("libVkLayer_fp_shaderfix.so", shims["zink"]),
                ("libfpg.so", ltw),
                ("libovrplatformcompat.so", platform_compat),
                ("libglshim.so", shims["gl"] and "libvrapi.so" in prefix_libs),
                ("libvrapi.so", shims["gl"] and "libvrapi.so" in prefix_libs),
            )
            if needed
        }
        shim_bytes.update(stub_bytes)

        def shim_patched_lib(name: str, data: bytes) -> bytes:
            if shims["vk"] and name in vk_targets:
                data, count = elf_replace_rodata_string(data, "libvulkan.so", "libfp_vk.so")
                log(f"Vulkan shim loaded by {name} ({count} dlopen site(s))")
            if shims["xrshim"] and name == "libOVRPlugin.so":
                data, count = elf_replace_rodata_string(data, "libopenxr_loader.so", "libframe_xrshim.so")
                log(f"XR shim loaded by {name} ({count} dlopen site(s))")
            if shims["zink"] and name in ("libUE4.so", "libUnreal.so", "libunity.so"):
                data = elf_add_needed(data, "libVkLayer_fp_shaderfix.so")
                log(f"Shader-fix Vulkan layer loaded by {name}")
            if shims["gl"] and name == "libvrapi.so" and name in prefix_libs:
                data = shim_bytes["libvrapi.so"]
                log("libvrapi.so replaced with the VrApi->OpenXR bridge")
            if name in gl_targets:
                data = elf_add_needed(data, "libglshim.so")
                log(f"GL shim loaded by {name}")
            if ltw and name == "libltw.so":
                data, count = elf_replace_rodata_string(data, "libEGL.so", "libfpg.so")
                if count:
                    log(f"{name} loads OpenGL ES through libfpg.so (depth formats)")
            if name.startswith("libmetaxraudio"):
                fixed = patch_metaxr_telemetry(data)
                if fixed:
                    data = fixed
                    log(f"Patched telemetry class lookup in {name}")
            if name == "libovrplatformloader.so":
                if platform_compat:
                    data = elf_add_needed(data, "libovrplatformcompat.so")
                    log("Linked libovrplatformcompat.so into the platform loader")
                if platform_missing:
                    data = elf_add_needed(data, "libovrstubs.so")
                    log("Linked ovr_* stand-ins into the platform loader")
            if avatar_stub is not None and name == "libovravatarloader.so":
                data = avatar_stub
            if blazerush and name == BLAZERUSH_LIB:
                data, notes = patch_blazerush(data)
                for note in notes:
                    log(f"BlazeRush: {note}")
            return data

        with zipfile.ZipFile(patched, "r") as src, zipfile.ZipFile(
            unsigned, "w", zipfile.ZIP_DEFLATED
        ) as dst:
            if loader not in src.namelist():
                raise RuntimeError(
                    "OVR Port output has no libopenxr_loader_generic.so; this APK/profile is incompatible"
                )
            if shims["vk"] and not vk_targets:
                log("No engine library loads libvulkan.so; the Vulkan shim settings will not take effect.")
            if shims["zink"] and not any(
                prefix + n in src.namelist() for n in ("libUE4.so", "libUnreal.so", "libunity.so")
            ):
                log("No known engine library starts EGL; the shader-fix layer will not take effect.")
            if shims["xrshim"] and prefix + "libOVRPlugin.so" not in src.namelist():
                log("No libOVRPlugin.so in the build; the XR shim will not be reached by the game.")
            for item in src.infolist():
                if item.filename == loader or item.filename.startswith("META-INF/"):
                    continue
                if (
                    item.filename.startswith(prefix)
                    and pathlib.Path(item.filename).name in CRACK_PAYLOAD_LIBS
                ):
                    log(f"Dropped crack payload {item.filename} (inert; tamper checks scan for it)")
                    continue
                if item.filename == "AndroidManifest.xml" and manifest_bytes is not None:
                    dst.writestr(item, manifest_bytes)
                    continue
                data = src.read(item.filename)
                if item.filename.startswith(prefix):
                    data = shim_patched_lib(pathlib.Path(item.filename).name, data)
                    if item.filename == prefix + "libopenxr_loader.so":
                        fixed, count = raise_swapchain_limit(data)
                        if fixed:
                            data = fixed
                            log(f"Swapchain size guard raised to 16384 px ({count} check(s))")
                dst.writestr(item, data)
            dst.writestr(prefix + "libopenxr_loader_original.so", src.read(loader))
            dst.write(adapter, loader)
            for name, blob in shim_bytes.items():
                dst.writestr(prefix + name, blob)
                log(f"Added FramePort shim {name}")
            dst.writestr(
                prefix + "libframe_settings.so",
                settings_bytes(scale, controllers, foveation, refresh_rate, recipe_settings),
            )

        aligned = work / "aligned.apk"
        run([tool_path(build, "zipalign"), "-f", "-P", "16", "4", unsigned, aligned])

        key = workspace / "signatures" / f"{package}.keystore"
        if not key.is_file():
            raise RuntimeError(
                f"OVR Port did not create the expected signing key: {key}"
            )

        run(
            [
                *apksigner_command(build, java),
                "sign",
                "--ks",
                key,
                "--ks-pass",
                "pass:password",
                "--out",
                game_apk,
                aligned,
            ]
        )
        run(
            [
                *apksigner_command(build, java),
                "verify",
                "--verbose",
                game_apk,
            ]
        )

        obb_count = copy_obbs(source_root, output_dir, package)
        settings_dict = {
            "scale": f"{scale / 100.0:.3f}",
            "controller_fix": "1" if controllers else "0",
            "foveation_fix": "1" if foveation else "0",
            "refresh_rate": str(refresh_rate),
        }
        settings_dict.update(recipe_settings)
        result = {
            "ok": True,
            "converted": True,
            "alreadyConverted": False,
            "package": package,
            "versionCode": version_code,
            "versionName": version_name,
            "applicationLabel": application_label,
            "gameApk": str(game_apk),
            "directory": str(output_dir),
            "obbCount": obb_count,
            "sha256": sha256(game_apk),
            "settings": settings_dict,
            "ovrportAppVersion": OVRPORT_APP_VERSION,
            "ovrportRuntimeVersion": OVRPORT_RUNTIME_VERSION,
            "frameportCommit": FRAMEPORT_COMMIT,
            "refreshRate": refresh_rate,
        }
        (output_dir / "frame-conversion.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def doctor() -> dict:
    result: dict[str, object] = {"ok": True}
    try:
        sdk = find_sdk()
        result["sdk"] = str(sdk)
        result["buildTools"] = str(find_build_tools(sdk))
    except Exception as exc:
        result["ok"] = False
        result["androidError"] = str(exc)
    try:
        result["java"] = str(find_java())
    except Exception as exc:
        result["ok"] = False
        result["javaError"] = str(exc)
    result["toolsRoot"] = str(tools_root())
    result["ovrportCached"] = (
        tools_root() / "ovrport-cli" / "overportcli-1.2.3-all.jar"
    ).is_file()
    adapter_path = tools_root() / "frame-adapter" / "libopenxr_loader_generic.so"
    result["adapterCached"] = adapter_path.is_file()
    result["adapterVerified"] = (
        adapter_path.is_file() and sha256(adapter_path) == FRAMEPORT_ADAPTER_SHA256
    )
    result["frameportCommit"] = FRAMEPORT_COMMIT
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Frame CyberDeck Quest APK converter")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")

    c = sub.add_parser("convert")
    c.add_argument("--input", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--scale", type=float, default=100.0)
    c.add_argument("--no-controller-fix", action="store_true")
    c.add_argument("--no-foveation-fix", action="store_true")
    c.add_argument(
        "--refresh-rate",
        type=int,
        default=0,
        choices=SUPPORTED_REFRESH_RATES,
        help="Requested headset refresh rate; 0 keeps the runtime default.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "doctor":
            emit(doctor())
            return
        if args.command == "convert":
            emit(
                convert(
                    pathlib.Path(args.input),
                    pathlib.Path(args.output),
                    scale=args.scale,
                    controllers=not args.no_controller_fix,
                    foveation=not args.no_foveation_fix,
                    refresh_rate=args.refresh_rate,
                )
            )
            return
        fail(f"Unknown command: {args.command}")
    except SystemExit:
        raise
    except Exception as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
