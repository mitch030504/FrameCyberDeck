#!/usr/bin/env python3
"""Vendor FramePort's engine and agent at a pinned commit.

Usage:
    python3 scripts/sync-frameport-engine.py [--check]

Without arguments the script downloads the pinned FramePort source tarball,
copies the build engine, catalog, artifacts, agent and bootstrap scripts into
vendor/frameport/, and writes PIN.json plus MANIFEST.sha256.

--check verifies the vendored tree against the manifest without touching the
network or the filesystem. CI runs this so a local edit or a stale vendor tree
cannot slip through unnoticed.

FramePort is GPL-3.0-only, like this project. See vendor/frameport/README.md.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor" / "frameport"
PIN = {
    "repository": "spoopyghosty0/frameport",
    "commit": "72c71fbbd303886992ab62cbc1aea6287100f935",
    "tag": "v1.4.1+1",
    "author_date": "2026-11-09T22:11:22Z",
    "source": "https://codeload.github.com/spoopyghosty0/frameport/tar.gz/{commit}",
}

# Paths copied verbatim from the upstream tree. `src/frameport/ui` is skipped:
# this app embeds the headless engine, not FramePort's Flet interface.
COPY_TREES = ("catalog", "artifacts", "agent", "bootstrap")
COPY_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")
ENGINE_TREE = "src/frameport"
ENGINE_SKIP_DIRS = {"ui", "__pycache__"}

# Files this repository adds next to the vendored tree; they are not part of
# the upstream manifest and are allowed by --check.
# Preserved across an update; never part of the upstream tarball.
LOCAL_FILES = {"PIN.json", "MANIFEST.sha256", "README.md", "requirements.txt"}
# Files this repository owns and does not include in the upstream manifest.
MANIFEST_SKIP = {"PIN.json", "MANIFEST.sha256", "README.md"}

# Local fixes applied after copying the upstream tree. Keep them small and
# documented; the manifest is generated after they run.
VENDOR_PATCHES = [
    (
        "src/frameport/analysis/elf.py",
        """    sec = elf.get_section_by_name(".dynsym")
    if isinstance(sec, SymbolTableSection) and sec["sh_type"] == "SHT_DYNSYM":
        return sec
    return next((s for s in elf.iter_sections() if s["sh_type"] == "SHT_DYNSYM"), None)
""",
        """    try:
        sec = elf.get_section_by_name(".dynsym")
        if isinstance(sec, SymbolTableSection) and sec["sh_type"] == "SHT_DYNSYM":
            return sec
        return next((s for s in elf.iter_sections() if s["sh_type"] == "SHT_DYNSYM"), None)
    except Exception:
        # Some shipped libraries have stripped or malformed section tables.
        return None
""",
    ),
    (
        "src/frameport/analysis/elf.py",
        """def needed(data: bytes) -> list[str]:
    elf = _elf(data)
    for sec in elf.iter_sections():
        if isinstance(sec, DynamicSection):
            return [t.needed for t in sec.iter_tags() if t.entry.d_tag == "DT_NEEDED"]
    return []
""",
        """def needed(data: bytes) -> list[str]:
    try:
        elf = _elf(data)
        for sec in elf.iter_sections():
            if isinstance(sec, DynamicSection):
                return [t.needed for t in sec.iter_tags() if t.entry.d_tag == "DT_NEEDED"]
    except Exception:
        return []
    return []
""",
    ),
    (
        "src/frameport/analysis/elf.py",
        """def soname(data: bytes) -> str | None:
    elf = _elf(data)
    for sec in elf.iter_sections():
        if isinstance(sec, DynamicSection):
            for t in sec.iter_tags():
                if t.entry.d_tag == "DT_SONAME":
                    return t.soname
    return None
""",
        """def soname(data: bytes) -> str | None:
    try:
        elf = _elf(data)
        for sec in elf.iter_sections():
            if isinstance(sec, DynamicSection):
                for t in sec.iter_tags():
                    if t.entry.d_tag == "DT_SONAME":
                        return t.soname
    except Exception:
        return None
    return None
""",
    ),
]


def apply_vendor_patches() -> None:
    for relative, old, new in VENDOR_PATCHES:
        path = VENDOR_DIR / relative
        text = path.read_text(encoding="utf-8")
        if old not in text:
            fail(f"vendored patch no longer applies: {relative}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def fail(message: str) -> None:
    print(f"sync-frameport-engine: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    members = archive.getmembers()
    for member in members:
        target = (destination / member.name).resolve()
        if target != root and root not in target.parents:
            fail(f"refusing to extract outside the temporary directory: {member.name}")
    archive.extractall(destination, members=members)


def download(commit: str, destination: Path) -> None:
    url = PIN["source"].format(commit=commit)
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "frame-cyberdeck-sync"})
    with urllib.request.urlopen(request, timeout=180) as response:
        with destination.open("wb") as out:
            shutil.copyfileobj(response, out)


def copy_tree(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in ENGINE_SKIP_DIRS for part in relative.parts):
            continue
        if path.is_dir():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def build_tree(upstream: Path) -> None:
    preserved: dict[str, bytes] = {}
    if VENDOR_DIR.exists():
        for name in LOCAL_FILES:
            path = VENDOR_DIR / name
            if path.is_file():
                preserved[name] = path.read_bytes()
        shutil.rmtree(VENDOR_DIR)
    VENDOR_DIR.mkdir(parents=True)

    engine_source = upstream / ENGINE_TREE
    if not engine_source.is_dir():
        fail(f"upstream tarball has no {ENGINE_TREE}")

    engine_target = VENDOR_DIR / ENGINE_TREE
    copy_tree(engine_source, engine_target)

    for name in COPY_TREES:
        source = upstream / name
        if not source.is_dir():
            fail(f"upstream tarball has no {name}/")
        copy_tree(source, VENDOR_DIR / name)

    for name in COPY_FILES:
        source = upstream / name
        if not source.is_file():
            fail(f"upstream tarball has no {name}")
        shutil.copy2(source, VENDOR_DIR / name)

    apply_vendor_patches()

    for name, data in preserved.items():
        (VENDOR_DIR / name).write_bytes(data)

    pin = dict(PIN)
    pin["vendored_at"] = "generated by scripts/sync-frameport-engine.py"
    (VENDOR_DIR / "PIN.json").write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")


def manifest_files() -> list[Path]:
    result = []
    for path in VENDOR_DIR.rglob("*"):
        relative = path.relative_to(VENDOR_DIR).as_posix()
        if not path.is_file() or relative in MANIFEST_SKIP:
            continue
        if "__pycache__" in relative or relative.endswith((".pyc", ".pyo")):
            continue
        result.append(path)
    return sorted(result)


def write_manifest() -> None:
    lines = [
        f"{sha256(path)}  {path.relative_to(VENDOR_DIR).as_posix()}" for path in manifest_files()
    ]
    (VENDOR_DIR / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check() -> None:
    if not VENDOR_DIR.is_dir():
        fail("vendor/frameport is missing; run the sync script without --check")
    pin_path = VENDOR_DIR / "PIN.json"
    manifest_path = VENDOR_DIR / "MANIFEST.sha256"
    if not pin_path.is_file() or not manifest_path.is_file():
        fail("vendor/frameport is incomplete (PIN.json or MANIFEST.sha256 missing)")

    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    if pin.get("commit") != PIN["commit"]:
        fail(f"PIN.json commit {pin.get('commit')} does not match the script pin {PIN['commit']}")

    expected: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        expected[relative] = digest

    actual = {
        path.relative_to(VENDOR_DIR).as_posix(): sha256(path) for path in manifest_files()
    }

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])

    if missing or extra or changed:
        for name in missing:
            print(f"missing: {name}", file=sys.stderr)
        for name in extra:
            print(f"untracked: {name}", file=sys.stderr)
        for name in changed:
            print(f"changed: {name}", file=sys.stderr)
        fail("vendored tree does not match MANIFEST.sha256; run the sync script to refresh")

    print(f"vendor/frameport matches {PIN['commit']} ({len(expected)} files)")


def update() -> None:
    with tempfile.TemporaryDirectory(prefix="frameport-sync-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "frameport.tar.gz"
        download(PIN["commit"], archive)
        extracted = temporary_path / "src"
        extracted.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            safe_extract(tar, extracted)
        roots = list(extracted.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            fail("unexpected upstream tarball layout")
        build_tree(roots[0])
    write_manifest()
    check()


def main() -> None:
    if "--check" in sys.argv[1:]:
        check()
    elif len(sys.argv) == 1:
        update()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
