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

        # FramePort shim injection (recipes by spoopyghosty0, GPLv3)
        shims = recipe_shims(recipe or {})
        shim_bytes = {
            name: ensure_frameport_artifact(root, name).read_bytes()
            for name, needed in (
                ("libframe_xrshim.so", shims["xrshim"]),
                ("libfp_vk.so", shims["vk"]),
                ("libVkLayer_fp_shaderfix.so", shims["zink"]),
            )
            if needed
        }
        if shims["gl"]:
            log("Recipe requests the GL shim; that flow needs FramePort's VrApi bridge and is not converted here.")

        # Which APK libraries the Vulkan shim rewrites (engines that load libvulkan.so by name).
        not_engine = ("libopenxr", "libOVR", "libovr", "libvrapi", "libfp_", "libframe", "libVkLayer", "libc++", "libunity")
        vk_targets: list[str] = []
        if shims["vk"]:
            with zipfile.ZipFile(patched) as probe:
                names = [n for n in probe.namelist() if n.startswith(prefix)]
                vk_targets = [
                    n
                    for n in names
                    if pathlib.Path(n).name in ("libUE4.so", "libUnreal.so")
                    or (
                        not pathlib.Path(n).name.startswith(not_engine)
                        and b"\0libvulkan.so\0" in probe.read(n)
                    )
                ]

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
                data = src.read(item.filename)
                if item.filename.startswith(prefix):
                    data = shim_patched_lib(pathlib.Path(item.filename).name, data)
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
