"""REAndroid's APKEditor (Apache-2.0, github.com/REAndroid/APKEditor): merges split APKs whose resource tables have
to be combined (language/density splits, feature modules) into one APK (sources/bundles.py). Downloaded on first
use into <data>/tools/ like OVRPort (newest release, at least MIN_VERSION: 1.4.3-1.4.8 corrupted some merged apps),
run by FramePort's Java. FRAMEPORT_APKEDITOR_JAR overrides it."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from ..core import cache
from ..core.paths import tools_dir
from . import toolchain

RELEASES = "https://api.github.com/repos/REAndroid/APKEditor/releases/latest"
MIN_VERSION = (1, 4, 9)
FALLBACK = ("1.4.9", "https://github.com/REAndroid/APKEditor/releases/download/V1.4.9/APKEditor-1.4.9.jar")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def latest() -> tuple[str, str]:
    """(version, jar URL) of the newest release, else the known-good one."""
    rel = cache.cached_json("apkeditor-release.json", RELEASES, max_age=7 * 86400) or {}
    if not rel.get("draft") and not rel.get("prerelease"):
        version = str(rel.get("tag_name") or "").lstrip("Vv")
        jar = next((a.get("browser_download_url") for a in rel.get("assets") or []
                    if str(a.get("name", "")).lower().endswith(".jar")), None)
        if version and jar and _version(version) >= MIN_VERSION:
            return version, jar
    return FALLBACK


def jar(progress=None) -> Path:
    if os.environ.get("FRAMEPORT_APKEDITOR_JAR"):
        return Path(os.environ["FRAMEPORT_APKEDITOR_JAR"])
    have = sorted(tools_dir().glob("apkeditor-*/APKEditor*.jar"), key=lambda p: _version(p.parent.name))
    if have and _version(have[-1].parent.name) >= MIN_VERSION:
        return have[-1]
    version, url = latest()
    dest = tools_dir() / f"apkeditor-{version}" / url.rsplit("/", 1)[-1]
    if not dest.exists():
        cache.download(url, dest, progress)
    return dest


def merge(apks: list[Path], out: Path, work: Path, timeout: float = 3600) -> Path:
    """Merge base + splits (exactly the APKs to keep: APKEditor merges every split it is given) into `out`
    (unsigned). APKEditor also drops isSplitRequired/requiredSplitTypes and Play's com.android.vending.* meta-data."""
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for i, apk in enumerate(apks):
        name = "base.apk" if i == 0 else f"split_{i:03d}.apk"
        try:
            os.link(apk, work / name)
        except OSError:
            shutil.copyfile(apk, work / name)
    out.unlink(missing_ok=True)
    p = toolchain.run_java(["-Xmx2g", "-jar", str(jar()), "m", "-i", str(work), "-o", str(out),
                            "-extractNativeLibs", "manifest"], timeout=timeout)
    if p.returncode or not out.is_file():
        raise RuntimeError("APKEditor couldn't merge the split APKs: " + (p.stdout + p.stderr)[-1500:])
    shutil.rmtree(work, ignore_errors=True)
    return out
