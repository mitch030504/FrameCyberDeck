"""Automatic SteamVR performance settings for PC VR games (patch pcvr.steamvr_tuning).

After a play session FramePort reads what SteamVR already records: the compositor's per-session summary
(vrcompositor.txt: presents / dropped / reprojected) and, when fpsVR is installed, its CPU/GPU frame-time histograms.
If the game dropped frames, the next Play picks the highest refresh rate the headset offers whose frame budget covers
the game's slow frames (99th percentile) and turns motion smoothing on, through SteamVR's own per-application settings
(fp_vrsettings.exe = OpenVR IVRSettings; SteamVR applies and keeps them). Nothing changes for games that keep up.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import winhost

DROP_LIMIT = 0.01  # more than 1 % of frames dropped: tune
HEADROOM = 1.05
# Rates SteamVR headsets commonly offer; used when the headset's own list isn't in the logs (only Steam Link prints it).
# SteamVR snaps a preferred rate to the nearest one the headset supports.
COMMON_RATES = (60.0, 72.0, 80.0, 90.0, 96.0, 100.0, 108.0, 120.0, 144.0)
SMOOTHING_FORCE_ON = 1  # SteamVR per-app motion smoothing: 0 global, 1 force on, 2 force off, 3 force always on
KEY_REFRESH = "preferredRefreshRate"
KEY_SMOOTHING = "motionSmoothingOverride"

_CONNECT = re.compile(r"External connection from (.+?) (\d+)\s*$")
_TOTAL = re.compile(r"- Total\.+\s*(\d+) presents\.\s*(\d+) dropped\.\s*(\d+) reprojected")
_TIMED_OUT = re.compile(r"- Timed out\.\s*(\d+) total")
_RATE = re.compile(r"vrlink: \t([0-9.]+) Hz")
_PREFERRED = re.compile(r"host preferred ([0-9.]+) Hz")


@dataclass
class Session:
    presents: int
    dropped: int
    reprojected: int
    timed_out: int = 0  # the compositor gave up waiting for the game's frame and showed the previous one again
    hz: float | None = None
    frame_ms_p99: float | None = None  # max(CPU, GPU) 99th percentile (fpsVR)

    @property
    def drop_ratio(self) -> float:
        """Frames that weren't new on time: dropped plus compositor timeouts (both show as judder)."""
        return (self.dropped + self.timed_out) / self.presents if self.presents else 0.0


def logs_dir(root: Path) -> Path:
    return root / "logs"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def compositor_session(text: str, exe_name: str) -> Session | None:
    """The last session of `exe_name` in a vrcompositor log (its "Total" line after the game's connection)."""
    last, current, cur = None, False, None
    for line in text.splitlines():
        m = _CONNECT.search(line)
        if m:
            current = Path(m.group(1).replace("\\", "/")).name.lower() == exe_name.lower()
            cur = None
            continue
        t = _TOTAL.search(line)
        if t and current:
            cur = last = Session(int(t.group(1)), int(t.group(2)), int(t.group(3)))
            continue
        o = _TIMED_OUT.search(line)
        if o and cur is not None:
            cur.timed_out = int(o.group(1))
            cur, current = None, False
    return last


def last_session(root: Path, exe_name: str) -> Session | None:
    for name in ("vrcompositor.txt", "vrcompositor.previous.txt"):
        s = compositor_session(_read(logs_dir(root) / name), exe_name)
        if s:
            return s
    return None


def _p99_ms(hist: list[int]) -> float | None:
    total = sum(hist)
    if not total:
        return None
    acc = 0
    for i, n in enumerate(hist):
        acc += n
        if acc >= total * 0.99:
            return i / 10.0  # fpsVR bins are 0.1 ms
    return None


def fpsvr_session(app_key: str, folder: Path | None = None, app_name: str | None = None) -> dict | None:
    """fpsVR's summary of the newest session of this app (hz, p99 frame time), if fpsVR is installed (optional).
    Matched by SteamVR app key, or by the program's name (the key changes when the Steam shortcut does)."""
    folder = folder or (winhost.env_path("LOCALAPPDATA") or Path("/nonexistent")) / "fpsVR"
    best = None
    for f in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if d.get("AppKey") == app_key or (app_name and str(d.get("app", "")).lower() == app_name.lower()):
            best = d
            break
    if not best:
        return None
    ms = [x for x in (_p99_ms(best.get("gputimes") or []), _p99_ms(best.get("cputimes") or [])) if x is not None]
    return {"hz": best.get("hz"), "frame_ms_p99": max(ms) if ms else None, "date": best.get("DateEnd")}


def available_rates(root: Path) -> tuple[float, ...]:
    """Refresh rates the headset offers (Steam Link lists them in vrserver.txt), else the common SteamVR rates."""
    rates = sorted({round(float(r)) for r in _RATE.findall(_read(logs_dir(root) / "vrserver.txt"))})
    return tuple(float(r) for r in rates) or COMMON_RATES


def current_rate(root: Path) -> float | None:
    """The rate the headset runs at: Steam Link's choice, else SteamVR's global preferredRefreshRate setting."""
    found = _PREFERRED.findall(_read(logs_dir(root) / "vrserver.txt"))
    if found:
        return float(found[-1])
    try:
        rate = json.loads(_read(root / "config" / "steamvr.vrsettings") or "{}").get("steamvr", {}).get(
            "preferredRefreshRate")
    except ValueError:
        rate = None
    return float(rate) if rate else None


def recommend(session: Session | None, rates: tuple[float, ...], current: float | None) -> dict | None:
    """{refresh, smoothing, reason} when the last session dropped frames, else None."""
    if not session or session.drop_ratio <= DROP_LIMIT:
        return None
    hz = session.hz or current or max(rates)
    lower = [r for r in rates if r < hz - 0.5] or [min(rates)]
    if session.frame_ms_p99:
        fits = [r for r in rates if 1000.0 / r >= session.frame_ms_p99 * HEADROOM]
        refresh = max(fits) if fits else min(rates)
        refresh = min(refresh, max(lower))  # always at least one step down from the rate that dropped frames
    else:
        refresh = max(lower)
    why = (f"{session.dropped + session.timed_out} of {session.presents} frames late or dropped "
           f"({session.drop_ratio:.1%}) at {hz:.0f} Hz"
           + (f"; slow frames take {session.frame_ms_p99:.1f} ms" if session.frame_ms_p99 else ""))
    return {"refresh": refresh, "smoothing": SMOOTHING_FORCE_ON, "reason": why}


def helper() -> Path | None:
    """fp_vrsettings.exe, copied next to FramePort's other Windows files (Windows programs run from Windows paths)."""
    from .paths import artifacts_dir

    src = artifacts_dir() / "win-x64" / "fp_vrsettings.exe"
    base = winhost.env_path("LOCALAPPDATA")
    if not src.exists() or not base:
        return None
    dst = base / "FramePort" / "fp_vrsettings.exe"
    try:
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    except OSError:
        return None
    return dst


def openvr_dll(root: Path) -> Path | None:
    for lib in winhost.steam_libraries(root):  # the libraries' steamapps folders
        p = lib / "common" / "SteamVR" / "bin" / "win64" / "openvr_api.dll"
        if p.exists():
            return p
    return None


def vrsettings(root: Path, ops: list[tuple[str, str, str, str, str | None]]) -> list[str]:
    """Run fp_vrsettings.exe with (op, section, key, type, value) operations; returns its output lines."""
    exe, dll = helper(), openvr_dll(root)
    if not exe or not dll:
        raise RuntimeError("SteamVR's openvr_api.dll or FramePort's settings helper is missing")
    args = [str(exe), winhost.to_windows(dll)]
    for op, section, key, kind, value in ops:
        args += [op, section, key, kind] + ([value] if op == "set" else [])
    p = subprocess.run(args, capture_output=True, text=True, timeout=60, **winhost._no_window())
    return [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]


def apply(root: Path, app_key: str, refresh: float | None, smoothing: int | None) -> list[str]:
    ops = []
    if refresh:
        ops.append(("set", app_key, KEY_REFRESH, "f", f"{refresh:.3f}"))
    if smoothing is not None:
        ops.append(("set", app_key, KEY_SMOOTHING, "i", str(smoothing)))
    return vrsettings(root, ops) if ops else []


def tune(root: Path, app_key: str, exe_name: str, params: dict) -> dict:
    """Decide and apply this game's SteamVR settings before it starts. Returns what was done (for the log/UI)."""
    fixed_rate = float(params.get("refresh") or 0)
    smoothing = params.get("smoothing", "auto")
    if fixed_rate or smoothing not in ("auto", None, ""):
        sm = {"on": SMOOTHING_FORCE_ON, "off": 2, "always": 3, "global": 0}.get(str(smoothing))
        return {"applied": apply(root, app_key, fixed_rate or None, sm), "reason": "set in the patch options"}
    session = last_session(root, exe_name)
    fps = fpsvr_session(app_key, app_name=Path(exe_name).stem)
    if session and fps:
        session.hz = fps.get("hz")
        session.frame_ms_p99 = fps.get("frame_ms_p99")
    rec = recommend(session, available_rates(root), current_rate(root))
    if not rec:
        return {"applied": [], "reason": "last session kept up" if session else "no session recorded yet"}
    return {"applied": apply(root, app_key, rec["refresh"], rec["smoothing"]), **rec}
