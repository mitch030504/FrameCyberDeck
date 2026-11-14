"""Classify a launch log: which milestones were reached, which known failure signatures appear, what to try."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import yaml

from ..core.paths import catalog_dir

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class Finding:
    id: str
    severity: str
    diagnosis: str
    suggest: list[str]
    evidence: str
    use_alt: bool = False
    question: str = ""  # a symptom only the player sees: its fix is offered as "Did … ? Yes = apply", never on its own
    report: bool = False  # nothing to switch on: diagnostics (a problem report) are what helps


@dataclass
class TriageResult:
    state: str  # RUNNING | EXITED | NEVER_STARTED | UNKNOWN
    milestone: str | None
    milestones: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    fps: float | None = None
    headless_end: str | None = None  # launch test: the session ended for lack of a worn headset (label), see triage()

    @property
    def verdict(self) -> str:
        fatal = [f for f in self.findings if f.severity == "fatal"]
        if not fatal and any(f.id == "frames-stopped" for f in self.findings):
            return "unknown"
        if (self.state == "RUNNING" or self.headless_end) and not fatal:
            return "pass"
        return "fail" if fatal or self.state in ("EXITED", "NEVER_STARTED") else "unknown"

    def suggestions(self) -> list[str]:
        """Fixes to offer without asking (findings with a symptom question are left to the player)."""
        out = []
        for f in self.findings:
            if f.question:
                continue
            for s in f.suggest:
                if s not in out:
                    out.append(s)
        return out


_db = None
_skipped: dict[str, str] = {}  # remote signature id -> why this app can't use it


def database() -> dict:
    """The bundled catalog/triage.yaml, updated by the copy on GitHub main (recommend/catalog.refresh_remote, every
    6 h) where this app can use it: a remote signature replaces the bundled one with its id, unless it is malformed,
    names a patch or adapter setting this app doesn't have, or needs a newer FramePort (min_app, also top-level); then
    the bundled one (if any) stays. Bundled-only signatures stay too (a source checkout or dev build may be ahead of
    main), and a bundled signature dated (`updated`) later than the remote one wins."""
    global _db
    if _db is None:
        bundled = yaml.safe_load((catalog_dir() / "triage.yaml").read_text(encoding="utf-8"))
        _skipped.clear()
        try:
            _db = merge_remote(bundled, _remote_text())
        except Exception:  # noqa: BLE001 - never let a remote copy break triage
            _db = bundled
    return _db


def reset() -> None:
    """Read the signatures again (a refresh downloaded a new remote copy)."""
    global _db
    _db = None


def skipped() -> dict[str, str]:
    """Remote signatures (or "*" = the whole remote copy, or a marker list) this app can't use, with the reason."""
    database()
    return dict(_skipped)


def _remote_text() -> str | None:
    from ..recommend import catalog

    return catalog.remote_triage_text()


SEVERITIES = ("fatal", "error", "warning", "info")
SIG_FIELDS = {"id": str, "pattern": str, "severity": str, "diagnosis": str, "suggest": list, "supersedes": list,
              "kind": str, "unless": str, "report": bool, "question": str, "use_alt": bool, "min_app": str,
              "updated": str, "unless_patch": str}
MARKER_FIELDS = {"id", "label", "pattern", "kind"}


def _too_new(min_app) -> str | None:
    from .. import __version__
    from ..updates import parse_version

    if min_app and parse_version(str(min_app)) > parse_version(__version__):
        return f"needs FramePort {min_app}"
    return None


def _regex_ok(p) -> bool:
    try:
        re.compile(p)
        return True
    except (re.error, TypeError):
        return False


def signature_problem(sig, known: set[str]) -> str | None:
    """Why this app can't use a (remote) signature, or None. Normalises YAML dates/numbers in updated/min_app."""
    if not isinstance(sig, dict):
        return "not a mapping"
    unknown = [k for k in sig if k not in SIG_FIELDS]
    if unknown:  # a field a newer FramePort reads: matching without it could give a wrong finding
        return f"unknown field(s) {', '.join(sorted(map(str, unknown)))}"
    for k in ("id", "pattern", "severity", "diagnosis"):
        if not sig.get(k):
            return f"missing {k}"
    for k in ("updated", "min_app"):
        if sig.get(k) is not None and not isinstance(sig[k], str):
            sig[k] = str(sig[k])
    for k, t in SIG_FIELDS.items():
        if sig.get(k) is not None and not isinstance(sig[k], t):
            return f"{k} is not a {t.__name__}"
    if sig["severity"] not in SEVERITIES:
        return f"unknown severity {sig['severity']}"
    if sig.get("kind", "quest") not in ("quest", "pcvr"):
        return f"unknown kind {sig['kind']}"
    if not _regex_ok(sig["pattern"]) or (sig.get("unless") and not _regex_ok(sig["unless"])):
        return "invalid pattern"
    if not all(isinstance(x, str) for x in (sig.get("suggest") or []) + (sig.get("supersedes") or [])):
        return "suggest/supersedes entries must be text"
    if reason := _too_new(sig.get("min_app")):
        return reason
    missing = [pid for pid, _v in map(split_suggestion, sig.get("suggest") or []) if pid not in known]
    return f"unknown patch(es) {', '.join(missing)}" if missing else None


def _markers_ok(items) -> bool:
    return isinstance(items, list) and all(
        isinstance(m, dict) and set(m) <= MARKER_FIELDS and isinstance(m.get("label"), str)
        and _regex_ok(m.get("pattern")) and m.get("kind", "quest") in ("quest", "pcvr") for m in items)


def merge_remote(bundled: dict, text: str | None) -> dict:
    """The bundled database with a remote copy's usable parts (see database())."""
    if not text:
        return bundled
    try:
        remote = yaml.safe_load(text)
    except yaml.YAMLError:
        _skipped["*"] = "not valid YAML"
        return bundled
    if not isinstance(remote, dict) or not isinstance(remote.get("signatures"), list):
        _skipped["*"] = "no signatures list"
        return bundled
    if reason := _too_new(remote.get("min_app")):
        _skipped["*"] = reason
        return bundled
    from ..patches import base

    base.load_all()  # every patch registered (overport ids come from this app's OVRPort list)
    known = set(base.REGISTRY)
    own = {s["id"]: s for s in bundled.get("signatures") or []}
    sigs, seen = [], set()
    for sig in remote["signatures"]:
        sid = sig.get("id") if isinstance(sig, dict) else None
        if not isinstance(sid, str) or not sid or sid in seen:
            _skipped[str(sid)] = "missing or repeated id"
            continue
        seen.add(sid)
        if reason := signature_problem(sig, known):
            _skipped[sid] = reason
            if sid in own:
                sigs.append(own[sid])  # this app's own version stays
            continue
        mine = own.get(sid)
        newer_here = mine is not None and str(mine.get("updated") or "") > str(sig.get("updated") or "")
        sigs.append(mine if newer_here else sig)
    sigs += [s for s in bundled.get("signatures") or [] if s["id"] not in seen]  # bundled-only ones stay
    out = dict(bundled, signatures=sigs)
    for key in ("milestones", "headless_end"):  # ordered lists without ids: the remote list as a whole, if valid
        if key in remote:
            if _markers_ok(remote[key]):
                out[key] = remote[key]
            else:
                _skipped[key] = "malformed list"
    return out


LOGCAT_LINE = re.compile(r"\d\d-\d\d \d\d:\d\d:\d\d\.\d+ ")


def game_lines(log: str, package: str | None = None) -> list[str]:
    """Strip colour codes; when possible keep only lines of the game's process (plus Lepton's own lines)."""
    lines = [ANSI.sub("", ln) for ln in log.splitlines()]
    if not package:
        return lines
    pids = set()
    for ln in lines:
        m = re.search(r"Start proc (\d+):" + re.escape(package), ln)
        if m:
            pids.add(m[1])
    if not pids:
        return lines
    # system_server's lines about the game's activities (which one was started / shown), e.g. Unreal's
    # DownloaderActivity (unreal-obb-check-stuck)
    own_activity = re.compile(r"(Displayed |START u0 .*cmp=)" + re.escape(package) + "/")
    out = []
    for ln in lines:
        f = ln.split()
        # logcat threadtime: date time pid tid level tag: msg; everything else is Lepton's own output (e.g. its short
        # "Boot complete!", which the container-not-started signature checks for: it used to be dropped here)
        if not (len(f) > 3 and LOGCAT_LINE.match(ln) and f[2].isdigit()):
            out.append(ln)
        elif (f[2] in pids or "lepton" in ln.lower() or "APP_ACTIVITY" in ln or " FramePortVideo" in ln
              or own_activity.search(ln)):
            # FramePortVideo: the hardware codec plugin logs from Android's media service, not the game's process
            out.append(ln)
    return out


# suggestions that only work with one graphics API: the Vulkan shim's settings for Vulkan games, the shader-fix layer
# under Zink for OpenGL ES games
API_ONLY = {"adapter.vk_shader_dump": "vulkan", "adapter.vk_shader_fix": "vulkan",
            "adapter.zink_shader_dump": "gles", "adapter.zink_shader_fix": "gles"}
SWAPCHAIN_FORMAT = re.compile(r"xrCreateSwapchain \d+x\d+ format=(\d+) .*result=0\b")


def graphics_api(text: str) -> str | None:
    """"vulkan" or "gles" from the swapchain formats FrameBridge logged: OpenXR passes the API's own format numbers,
    Vulkan's VkFormat values are small (e.g. 43 = sRGB), GL's internal formats are 0x8000+ (35907 = GL_SRGB8_ALPHA8).
    None when the log has no (or both kinds of) swapchains."""
    kinds = {"gles" if int(m) >= 0x1000 else "vulkan" for m in SWAPCHAIN_FORMAT.findall(text)}
    return kinds.pop() if len(kinds) == 1 else None


def split_suggestion(s: str) -> tuple[str, str | None]:
    """A suggestion is a patch id, or a value for an adapter setting ("adapter.focus_hold_ms=2500")."""
    pid, eq, value = s.partition("=")
    return pid.strip(), (value.strip() if eq else None)


def applied_patches(package: str | None) -> set[str]:
    """The patches in the game's current build (library entry build.applied); empty when unknown."""
    if not package:
        return set()
    try:
        from ..core import library

        return set(((library.game(package) or {}).get("build") or {}).get("applied") or [])
    except Exception:  # noqa: BLE001 - no library (e.g. a diagnostics zip read elsewhere): nothing known
        return set()


def triage(log: str, state: str = "UNKNOWN", package: str | None = None, crash: str = "",
           kernel: str = "", headless: bool = False, applied: set[str] | None = None) -> TriageResult:
    """`crash` = the container's crash logcat (tombstones come from crash_dump's pid, so it isn't pid-filtered);
    `kernel` = kernel log lines of a play session ("kernel: …", GPU hangs; agent session_log); `headless` = a launch
    test (no headset worn): what happens after a `headless_end` line doesn't count against the game. `applied` = the
    patches in the build that ran (default: the library's record for `package`): a signature with `unless_patch`
    doesn't fire when that patch is in it (its log line stays the same with the fix in place)."""
    db = database()
    if applied is None and any(sig.get("unless_patch") for sig in db["signatures"]):
        applied = applied_patches(package)
    kind = "pcvr" if package and package.startswith("rift.") else "quest"  # Proton/Revive logs vs Lepton logcat
    lines = game_lines(log, package) if kind == "quest" else [ANSI.sub("", ln) for ln in log.splitlines()]
    lines += [ANSI.sub("", ln) for ln in crash.splitlines()]
    lines += [ANSI.sub("", ln) for ln in kernel.splitlines()]
    text = "\n".join(lines)
    res = TriageResult(state, None)
    for m in db["milestones"]:
        if m.get("kind", "quest") != kind:
            continue
        if re.search(m["pattern"], text):
            res.milestones.append(m["label"])
            res.milestone = m["label"]
    for sig in db["signatures"]:
        if sig.get("kind", "quest") != kind:
            continue
        hit = re.search(sig["pattern"], text)
        if hit and sig.get("unless") and re.search(sig["unless"], text):
            hit = None  # e.g. a rejected swapchain the adapter retried successfully
        gate = sig.get("unless_patch")
        if hit and gate and set([gate] if isinstance(gate, str) else gate) & (applied or set()):
            hit = None  # the fix is already in the build
        if hit:
            line = next((ln for ln in lines if re.search(sig["pattern"], ln)), hit.group(0))
            res.findings.append(Finding(sig["id"], sig["severity"], sig["diagnosis"], list(sig.get("suggest") or []),
                                        line.strip()[:300], bool(sig.get("use_alt")), sig.get("question") or "",
                                        bool(sig.get("report"))))
    api = graphics_api(text)
    if api:  # a setting for the other graphics API can't help (e.g. the GPU-hang shader dumps)
        for f in res.findings:
            f.suggest = [s for s in f.suggest if API_ONLY.get(split_suggestion(s)[0], api) == api]
    # a root-cause finding hides the generic crash findings it explains
    hidden = {h for sig in db["signatures"] if any(f.id == sig["id"] for f in res.findings)
              for h in sig.get("supersedes") or []}
    res.findings = [f for f in res.findings if f.id not in hidden]
    if headless:
        _forgive_headless_end(res, lines, db.get("headless_end") or [])
    fps = re.findall(r"pacing: ([0-9.]+) fps", text)
    if fps:
        res.fps = float(fps[-1])
    stopped = frames_stopped(lines)
    has_pacing = any(re.search(r"FrameBridge\s*: pacing:", ln) for ln in lines)
    if (stopped and has_pacing and state == "RUNNING" and not res.headless_end
            and not any(f.severity == "fatal" for f in res.findings)):
        res.findings.append(Finding(
            "frames-stopped", "fatal",
            f"The game stopped sending frames {stopped:.0f} s before the test ended although its process kept "
            "running (a crashed or stuck render thread: a frozen picture in the headset).", [],
            "last FrameBridge pacing line"))
    elif (stopped and not has_pacing and state in ("RUNNING", "UNKNOWN") and not res.headless_end
          and not any(f.severity == "fatal" for f in res.findings)
          and not any(re.search(m["pattern"], text) for m in db.get("headless_end") or [])):
        res.findings.append(Finding(
            "frames-stopped", "info",
            "The game submitted projection frames but produced no FrameBridge pacing summary for at least "
            f"{stopped:.0f} s while its process appeared alive. It may have stopped drawing; collect diagnostics "
            "while the picture is stuck to investigate.", [],
            "FrameBridge projection frames without a pacing summary", report=True))
    return res


def _forgive_headless_end(res: TriageResult, lines: list[str], markers: list[dict]) -> None:
    """A launch test whose game sent paced frames until a `headless_end` line (the VR session ended because nobody
    wears the headset, e.g. the VrApi bridge's tracking deadline): an exit or crash after it is an artifact of the
    test (BlazeRush: zink DEVICE LOST + SIGSEGV in the GPU driver thread as the renderer tore down, GitHub #57).
    Findings first seen after that line become one info finding; the verdict then passes (TriageResult.verdict)."""
    hits = ((m, next((i for i, ln in enumerate(lines) if re.search(m["pattern"], ln)), None)) for m in markers)
    marker, at = next(((m, i) for m, i in hits if i is not None), (None, None))
    if at is None or not any("FrameBridge: pacing:" in ln for ln in lines[:at]):
        return  # no such line, or no frames before it: the game never really ran
    when = _secs(lines[at])

    def after(f: Finding) -> bool:
        i = next((i for i, ln in enumerate(lines) if f.evidence and f.evidence in ln.strip()), None)
        if i is None:
            return False
        t = _secs(lines[i])
        return t >= when if t is not None and when is not None else i > at

    late = [f for f in res.findings if after(f)]
    res.findings = [f for f in res.findings if f not in late]
    res.headless_end = marker["label"]
    what = ", ".join(f.id for f in late) or "the game ended"
    res.findings.append(Finding(
        "headless-end", "info",
        f"The game ran (paced frames) until the VR session ended for lack of a worn headset ({marker['label']}). What "
        f"came after ({what}) is a side effect of this headless test and doesn't happen in the headset.", [],
        lines[at].strip()[:300]))


def _secs(line: str) -> float | None:
    """A logcat line's time in seconds, its date included (as a day of a leap year): logcat replays old kernel
    lines dated days before the game's (BattleTalent, GitHub #293), so the time of day alone can't order them."""
    m = LOGCAT_TIME.match(line)
    if not m:
        return None
    month, day = int(m[1]), int(m[2])
    try:
        ordinal = date(2000, month, day).timetuple().tm_yday
    except ValueError:
        return None
    return (ordinal * 24 + int(m[3])) * 3600 + int(m[4]) * 60 + int(m[5]) + int(m[6]) / 1000


MISSING_OBB = ("The game keeps its content in a data file (.obb) and none was installed: FramePort found no .obb "
               "files next to its APK. Games can stop silently (Unreal) or crash during engine initialization "
               "(CryEngine, for example The Climb). Put "
               "the .obb files in a folder named like the package (or obb/) next to the APK, add the folder again "
               "and reinstall.")


def add_missing_obb(res: TriageResult) -> TriageResult:
    """A launch test of a game whose APK expects an OBB that wasn't installed (from the library, not the log: the
    game logs nothing about it). Only when it sent no frames: an OBB from an earlier install may still be there."""
    if res.fps is None and not any(f.id == "missing-obb" for f in res.findings):
        res.findings.insert(0, Finding("missing-obb", "fatal", MISSING_OBB, [],
                                       "analysis: the APK expects an OBB; no data folder"))
    return res


LOGCAT_TIME = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})")


_YEAR = 366 * 86400
_HALF_YEAR = _YEAR / 2


def frames_stopped(lines: list[str], gap: float = 20.0) -> float | None:
    """Seconds since the last pacing summary (or initial projection submission if none), when over `gap`.
    Twenty seconds allows several of FrameBridge's 5 s pacing windows before suspecting a stalled frame loop."""
    last_projection = last_pacing = last = None
    for ln in lines:  # logcat lines aren't strictly in time order: the latest time counts, not the last line
        t = _secs(ln)
        if t is None:
            continue
        if last is not None and t < last - _HALF_YEAR:  # past New Year (12-31 -> 01-01)
            t += _YEAR
        last = t if last is None else max(last, t)
        if re.search(r"FrameBridge\s*: pacing:", ln):
            last_pacing = t if last_pacing is None else max(last_pacing, t)
        if re.search(r"FrameBridge\s*: (?:new layer: type=35\b|projection view \d+:)", ln):
            last_projection = t if last_projection is None else max(last_projection, t)
    if last_pacing is None:
        last_pacing = last_projection
    if last_pacing is None or last is None:
        return None
    delta = last - last_pacing
    return delta if delta > gap else None
