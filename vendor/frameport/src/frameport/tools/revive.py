"""Revive (LibreVR) as a portable tool: the latest ReviveInstaller.exe is downloaded from GitHub and the files FramePort
needs are unpacked from it in pure Python (no installer run, no admin rights, works on every host OS).

Revive only ships as an NSIS installer. Its data is non-solid deflate, so each file is a separate deflate stream; we
read the NSIS header (entries + string table) to map file names to data offsets. Only the runtime files are kept:
ReviveInjector.exe, LibRevive{32,64}.dll, LibReviveXR{32,64}.dll, openvr_api{32,64}.dll and the Input/ bindings.
Which Revive is used, in order: FRAMEPORT_REVIVE_DIR (a folder containing ReviveInjector.exe), a Revive the user
installed on this Windows PC (the official installer: registry HKLM/HKCU\\Software\\Revive, default
C:\\Program Files\\Revive), then FramePort's own portable copy (downloaded when first needed).
"""
from __future__ import annotations

import json
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from ..core import cache
from ..core.paths import tools_dir, write_atomic

REVIVE_RELEASE = "https://api.github.com/repos/LibreVR/Revive/releases/latest"
INSTALLER = "ReviveInstaller.exe"
RUNTIME_FILES = ("ReviveInjector.exe", "LibRevive32.dll", "LibRevive64.dll", "LibReviveXR32.dll", "LibReviveXR64.dll",
                 "openvr_api32.dll", "openvr_api64.dll", "LICENSE")
KEEP_DIRS = ("Input",)

NSIS_SIG = b"\xef\xbe\xad\xdeNullsoftInst"
EW_CREATEDIR, EW_EXTRACTFILE = 11, 20  # NSIS 3 opcodes (SetOutPath, File)
NS_LANG, NS_SHELL, NS_VAR, NS_SKIP = 1, 2, 3, 4  # Unicode NSIS 3 special codes, each followed by one 16-bit param
VAR_INSTDIR, VAR_OUTDIR_BASE = 21, 31  # $INSTDIR, $_OUTDIR (File /r base folder; equals $INSTDIR here)


class NsisError(Exception):
    pass


@dataclass
class NsisFile:
    path: str  # relative to $INSTDIR (backslashes turned into /)
    offset: int  # absolute offset of the block (4-byte length prefix) in the installer


def _read_block(data: bytes, offset: int) -> bytes:
    n = struct.unpack_from("<I", data, offset)[0]
    raw = data[offset + 4:offset + 4 + (n & 0x7FFFFFFF)]
    return zlib.decompressobj(-15).decompress(raw) if n & 0x80000000 else raw


def nsis_files(data: bytes) -> list[NsisFile]:
    """Files an NSIS 3 (Unicode, non-solid deflate) installer extracts, in script order."""
    sig = data.find(NSIS_SIG)
    if sig < 4:
        raise NsisError("not an NSIS installer")
    start = sig + 24  # after firstheader: flags, siginfo, magic, header length, total length
    first = struct.unpack_from("<I", data, start)[0]
    if not first & 0x80000000:
        raise NsisError("unsupported NSIS layout (solid or uncompressed header)")
    try:
        header = _read_block(data, start)
    except zlib.error as exc:
        raise NsisError(f"unsupported NSIS compression: {exc}") from None
    data_base = start + 4 + (first & 0x7FFFFFFF)
    blocks = [struct.unpack_from("<II", header, 4 + 8 * k) for k in range(8)]
    (ent_off, n_ent), (str_off, _) = blocks[2], blocks[3]
    str_end = blocks[4][0]
    strings = header[str_off:str_end]
    if strings[:2] != b"\0\0":
        raise NsisError("ANSI NSIS installers are not supported")

    def text(index: int) -> str:
        out, k = [], index * 2
        while k + 2 <= len(strings):
            c = struct.unpack_from("<H", strings, k)[0]
            k += 2
            if c == 0:
                break
            if c in (NS_LANG, NS_SHELL, NS_VAR, NS_SKIP):
                param = struct.unpack_from("<H", strings, k)[0]
                k += 2
                if c == NS_SKIP:
                    out.append(chr(param))
                elif c == NS_VAR and ((param & 0x7F) | ((param & 0x7F00) >> 1)) in (VAR_INSTDIR, VAR_OUTDIR_BASE):
                    out.append("\1")  # the install folder
                else:
                    out.append("\0")  # another variable / shell folder / language string
                continue
            out.append(chr(c))
        return "".join(out)

    files, outdir = [], ""
    for k in range(n_ent):
        which, *parm = struct.unpack_from("<7I", header, ent_off + 28 * k)
        if which == EW_CREATEDIR:
            path = text(parm[0])
            # only folders under the install folder; others ($PLUGINSDIR, $TEMP, ...) are ignored
            ok = path.startswith("\1") and "\0" not in path and "\1" not in path[1:]
            outdir = path[1:].strip("\\").replace("\\", "/") if ok else None
        elif which == EW_EXTRACTFILE and outdir is not None:
            name = text(parm[1])
            if "\0" in name or "\1" in name:
                continue
            files.append(NsisFile(f"{outdir}/{name}" if outdir else name, data_base + parm[2]))
    return files


def extract(installer: Path, dest: Path, wanted=None) -> list[str]:
    """Unpack the selected files (default: Revive's runtime files) into dest. Returns the relative paths written."""
    data = installer.read_bytes()
    keep = wanted or (lambda p: p in RUNTIME_FILES or p.split("/")[0] in KEEP_DIRS)
    written = []
    for f in nsis_files(data):
        if not keep(f.path) or f.path in written:
            continue
        target = dest / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_read_block(data, f.offset))
        written.append(f.path)
    missing = [n for n in RUNTIME_FILES if n not in written] if wanted is None else []
    if missing:
        raise NsisError(f"installer is missing {', '.join(missing)}")
    return written


# ------------------------------------------------------------------------------------------ tool management
def _state_file() -> Path:
    return tools_dir() / "revive.json"


def _usable(path: Path | None) -> bool:
    return bool(path) and (path / "ReviveInjector.exe").is_file() and (path / "LibReviveXR64.dll").is_file()


_system_cache: tuple[float, Path | None] | None = None


def system_revive(max_age: float = 60) -> Path | None:
    """A Revive installed with the official installer on this Windows PC (also found from WSL). The default folder is
    checked first; the registry (slow from WSL) only when it isn't there. Cached for max_age seconds."""
    import time

    from ..core import winhost

    global _system_cache
    if _system_cache and time.time() - _system_cache[0] < max_age:
        return _system_cache[1]
    found = None
    if winhost.available():
        defaults = [Path(os.environ[v]) / "Revive" for v in ("ProgramW6432", "ProgramFiles") if os.environ.get(v)] \
            if winhost.is_windows() else []
        defaults.append(winhost.to_local(r"C:\Program Files\Revive"))
        found = next((c for c in defaults if _usable(c)), None)
        if not found:
            for hive in ("HKLM", "HKCU"):
                v = winhost.reg_query(hive + r"\Software\Revive", "")
                try:
                    candidate = winhost.to_local(v.strip('"')) if v else None
                except ValueError:
                    candidate = None
                if candidate and _usable(candidate):
                    found = candidate
                    break
    _system_cache = (time.time(), found)
    return found


def release_for(path: Path) -> str | None:
    """Revive release a folder's files came from: the first GitHub release published on/after LibReviveXR64.dll was
    built (Revive's version resources are stale: the DLLs carry the LibOVR version, the dashboard says 3.0.0.0)."""
    import datetime as dt

    try:
        built = dt.datetime.fromtimestamp((path / "LibReviveXR64.dll").stat().st_mtime, dt.UTC)
    except OSError:
        return None
    rels = cache.cached_json("revive-releases.json", REVIVE_RELEASE.rsplit("/", 1)[0] + "?per_page=50",
                             max_age=7 * 86400) or []
    best = None
    for r in rels:
        try:
            when = dt.datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        if when >= built - dt.timedelta(days=1) and (best is None or when < best[0]):
            best = (when, r.get("tag_name"))
    return best[1] if best else None


def _managed() -> tuple[Path, str] | None:
    try:
        st = json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return None
    path = Path(st.get("path", ""))
    return (path, st.get("version")) if _usable(path) else None


def source() -> str | None:
    """Where the Revive in use comes from: "env" | "system" | "managed" | None."""
    if os.environ.get("FRAMEPORT_REVIVE_DIR"):
        return "env" if _usable(Path(os.environ["FRAMEPORT_REVIVE_DIR"])) else None
    if system_revive():
        return "system"
    return "managed" if _managed() else None


def revive_dir() -> Path | None:
    """Folder with ReviveInjector.exe + the Revive DLLs, or None."""
    src = source()
    if src == "env":
        return Path(os.environ["FRAMEPORT_REVIVE_DIR"])
    if src == "system":
        return system_revive()
    return _managed()[0] if src == "managed" else None


def installed_version() -> str | None:
    src = source()
    if src == "managed":
        return _managed()[1]
    if src in ("env", "system"):
        return release_for(revive_dir()) or src
    return None


def latest() -> tuple[str, str] | None:
    rel = cache.cached_json("revive-release.json", REVIVE_RELEASE, max_age=24 * 3600)
    if not rel:
        return None
    asset = next((a for a in rel.get("assets", []) if a["name"] == INSTALLER), None)
    return (rel["tag_name"], asset["browser_download_url"]) if asset else None


def install(progress=None, force: bool = False) -> Path:
    """FramePort's portable copy (skipped when a Revive is already installed on the system, unless force)."""
    if not force and source() in ("env", "system"):
        return revive_dir()
    found = latest()
    if not found:
        raise RuntimeError("could not reach GitHub to find the latest Revive release")
    version, url = found
    dest = tools_dir() / f"revive-{version}"
    if not (dest / "ReviveInjector.exe").exists():
        archive = cache.download(url, tools_dir() / f"ReviveInstaller-{version}.exe", progress)
        tmp = dest.with_name(dest.name + ".tmp")
        extract(archive, tmp)
        tmp.replace(dest)
        archive.unlink(missing_ok=True)
    write_atomic(_state_file(), json.dumps({"version": version, "path": str(dest)}, indent=2))
    return dest


def runtime_files(root: Path | None = None) -> list[Path]:
    """Every file under the Revive folder that a game install needs (for packing onto the Frame)."""
    root = root or revive_dir()
    if not root:
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and
                  (p.relative_to(root).as_posix() in RUNTIME_FILES or p.relative_to(root).parts[0] in KEEP_DIRS))
