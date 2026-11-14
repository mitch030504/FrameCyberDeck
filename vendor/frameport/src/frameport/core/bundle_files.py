"""The file list of a packaged FramePort (GitHub #476).

`scripts/package.py` writes `_bundle_files.txt` into the bundle's `frameport` package folder after `flet build`: one
line per file of the package (path relative to it, '/'-separated), with the size for compiled modules. A Windows
update copies the new files over the old ones, so an update that went wrong — or one made by an older version's
update script, which leaves the old version's files behind — can mix two versions: Python then imports whatever
.pyc it finds (`patches.base` imports every module in patches/frame). At start the app compares its own files with
the list: files the build doesn't have are removed (or at least never imported), and changed or missing compiled
modules mean the install is broken, which the GUI says plainly instead of crashing somewhere later.

No list (source checkout, wheel, PyInstaller bundle) = nothing to check."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

NAME = "_bundle_files.txt"
PKG = Path(__file__).resolve().parents[1]
SIZED = (".pyc", ".py")  # program files signing never changes (.exe/.dll get signed after the list is written)

_log = logging.getLogger(__name__)


@dataclass
class Check:
    stale: list[str] = field(default_factory=list)  # files the build doesn't have
    removed: list[str] = field(default_factory=list)  # … of which deleted now
    mismatched: list[str] = field(default_factory=list)  # compiled modules missing or of another size

    @property
    def broken(self) -> bool:
        """The install mixes versions and can't be trusted: reinstall by hand."""
        return bool(self.mismatched) or len(self.removed) < len(self.stale)


def write(pkg: Path) -> int:
    """Packaging: list every file of the built package folder `pkg`. Returns the number of files."""
    lines = []
    for root, dirs, files in os.walk(pkg):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            path = Path(root) / name
            rel = path.relative_to(pkg).as_posix()
            if rel == NAME:
                continue
            lines.append(f"{rel}\t{path.stat().st_size}" if name.endswith(SIZED) else rel)
    lines.sort()
    (pkg / NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def read(pkg: Path = PKG) -> dict[str, int | None] | None:
    """{relative path: size or None}, or None without a list."""
    try:
        text = (pkg / NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    out: dict[str, int | None] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        rel, _, size = line.partition("\t")
        try:
            out[rel.lower()] = int(size) if size else None
        except ValueError:
            out[rel.lower()] = None
    return out


_stale: set[str] = set()


def check(pkg: Path = PKG, repair: bool = True) -> Check | None:
    """Compare the package folder with its list; with `repair`, delete files the build doesn't have — but only when
    the build's own compiled modules all match the list (then the list belongs to the code that runs)."""
    files = read(pkg)
    if files is None:
        return None
    result = Check()
    seen = set()
    for root, dirs, names in os.walk(pkg):
        dirs[:] = [d for d in dirs if d != "__pycache__"]  # only used next to a .py source; bundles have none
        for name in names:
            path = Path(root) / name
            rel = path.relative_to(pkg).as_posix()
            key = rel.lower()
            if key == NAME.lower():
                continue
            seen.add(key)
            if key not in files:
                result.stale.append(rel)
                continue
            size = files[key]
            if size is not None:
                try:
                    if path.stat().st_size != size:
                        result.mismatched.append(rel)
                except OSError:
                    result.mismatched.append(rel)
    result.mismatched += sorted(rel for rel, size in files.items() if size is not None and rel not in seen)
    _stale.clear()
    _stale.update(rel.lower() for rel in result.stale)
    if repair and result.stale and not result.mismatched:
        for rel in result.stale:
            try:
                (pkg / rel).unlink()
                result.removed.append(rel)
            except OSError as exc:
                _log.warning("couldn't remove %s left from another FramePort version: %s", rel, exc)
        _stale.difference_update(rel.lower() for rel in result.removed)
    return result


def is_stale(module_file: str | Path, pkg: Path = PKG) -> bool:
    """A module file left from another version that check() found and couldn't remove (never import it)."""
    if not _stale:
        return False
    try:
        rel = Path(module_file).resolve().relative_to(pkg).as_posix().lower()
    except (ValueError, OSError):
        return False
    return rel in _stale
