"""Room storage and offline scanner operations on a connected Frame. No Flet."""
from __future__ import annotations

import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..core.paths import artifacts_dir, user_data_dir
from ..frame.connection import sh_quote
from ..mr.room import dumps, parse, preview_svg, read_mesh
from .livestream import sync_helper


def status(frame) -> dict:
    return frame.agent("room_status")


def delete(frame, universe: int | None = None) -> dict:
    return frame.agent("room_delete", **({"universe": universe} if universe is not None else {}))


def from_boundary(frame, height: float = 2.5) -> dict:
    return frame.agent("room_from_boundary", height=height)


def capture(frame, seconds: float = 90, wait: float = 180, preview: bool = True) -> dict:
    """Start a scan in the headset (the agent records the passthrough mesh, then builds the room)."""
    ensure_scanner(frame)
    ensure_prompts(frame)
    result = frame.agent("room_capture", seconds=seconds, wait=wait, preview=preview)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def capture_status(frame) -> dict:
    return frame.agent("room_capture_status")


SCAN_DONE = ("done", "failed", "none")


class ScanCancelled(RuntimeError):
    """The scan was cancelled (cancel_scan, the Room tab's Cancel)."""


def cancel_scan(frame) -> dict:
    """Stop a running scan in the headset; the saved room stays."""
    return frame.agent("room_capture_stop")


def scan(frame, seconds: float = 90, wait: float = 180, progress=None, poll: float = 2.0,
         sleep=time.sleep, preview: bool = True) -> dict:
    """capture() and wait for the result. progress(status) gets every status the agent reports (state waiting ->
    recording -> building -> done|failed; directions seen, looked_up). Returns the final status; raises on failure."""
    capture(frame, seconds, wait, preview=preview)
    deadline = time.monotonic() + wait + seconds + 600  # + building time
    while True:
        st = capture_status(frame)
        if progress:
            progress(st)
        if st.get("state") in SCAN_DONE:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("The room scan didn't finish")
        sleep(poll)
    if st.get("cancelled") or st.get("error") == "cancelled":
        raise ScanCancelled("The room scan was cancelled")
    if st.get("state") != "done":
        raise RuntimeError(st.get("error") or "The room scan didn't finish")
    return st


def ensure_prompts(frame) -> str:
    """The in-headset scan prompts (artifacts/roomscan/*.png) next to the scanner, synced by digest in one go."""
    import hashlib

    local = artifacts_dir() / "roomscan"
    remote = f"{frame.home}/.local/share/frameport/bin/roomscan-prompts"
    files = sorted(local.glob("*.png"))
    if not files:
        return remote
    want = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    _, out, _ = frame.run(f"mkdir -p {sh_quote(remote)} && cd {sh_quote(remote)} && sha256sum -- *.png 2>/dev/null")
    have = {}
    for line in out.splitlines():
        digest, _, name = line.partition("  ")
        have[name.strip()] = digest
    for f in files:
        if have.get(f.name) != want[f.name]:
            frame.put(f, f"{remote}/{f.name}", resume=False)
    return remote


@contextmanager
def _upload_dir(frame):
    remote = f"{frame.home}/.local/share/frameport/rooms/.incoming-{uuid.uuid4().hex}"
    code, _, err = frame.run(f"mkdir -p {sh_quote(remote)}")
    if code:
        raise RuntimeError(err.strip())
    try:
        yield remote
    finally:
        frame.run(f"rm -rf -- {sh_quote(remote)}")


def put(frame, path: Path) -> dict:
    path = Path(path)
    room = parse(path.read_text(encoding="utf-8"))
    mesh_path = None
    if room.mesh:
        mesh_path = path.parent / room.mesh.filename
        mesh = read_mesh(mesh_path.read_bytes())
        if (mesh.vertex_count, mesh.index_count) != (room.mesh.vertex_count, room.mesh.index_count):
            raise ValueError("room mesh counts do not match room.txt")
    staging = user_data_dir() / "rooms"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="import-", dir=staging) as local, _upload_dir(frame) as remote:
        canonical = Path(local) / "room.txt"
        canonical.write_text(dumps(room), encoding="utf-8")
        frame.put(canonical, f"{remote}/room.txt", resume=False)
        if mesh_path:
            frame.put(mesh_path, f"{remote}/{room.mesh.filename}", resume=False)
        return frame.agent("room_put", path=f"{remote}/room.txt")


def export(frame, directory: Path) -> Path:
    st = status(frame)
    if not st.get("room_text"):
        raise ValueError("No room is stored on the Frame")
    room = parse(st["room_text"])
    directory = Path(directory)
    mesh_data = None
    if room.mesh:
        remote = f"{frame.home}/.local/share/frameport/rooms/{room.universe}/{room.mesh.filename}"
        with frame.sftp.open(remote, "rb") as f:
            mesh_data = f.read(64 * 1024 * 1024)
        mesh = read_mesh(mesh_data)
        if (mesh.vertex_count, mesh.index_count) != (room.mesh.vertex_count, room.mesh.index_count):
            raise ValueError("room changed during export; try again")
    directory.mkdir(parents=True, exist_ok=True)
    if mesh_data is not None:
        (directory / room.mesh.filename).write_bytes(mesh_data)
    path = directory / "room.txt"
    path.write_text(dumps(room), encoding="utf-8")
    return path


def ensure_scanner(frame) -> str:
    local = artifacts_dir() / "linux-arm64-bin" / "fp_roomscan"
    if not local.is_file():
        raise RuntimeError("This build does not include the room scanner yet")
    return sync_helper(frame, local, "fp_roomscan")


def build(frame, dump: Path, universe: int | None = None) -> dict:
    dump = Path(dump)
    with dump.open("rb") as f:
        if f.read(8) != b"FPRS\x01\0\0\0":
            raise ValueError("expected FPRS version 1 scan dump")
    ensure_scanner(frame)
    with _upload_dir(frame) as remote:
        frame.put(dump, f"{remote}/scan.fprs", resume=False)
        return frame.agent("room_build", dump=f"{remote}/scan.fprs",
                           **({"universe": universe} if universe is not None else {}))


# ---------------------------------------------------------------- scan clean-up (fp_roomscan's refinement)
# The one list of clean-up settings: the owner's tuned defaults (compiled into the scanner; the agent's
# ROOM_REFINE_DEFAULTS repeats them, a test keeps both equal) and what the Room tab shows for each one.
# kind: choice / bool / number; unit: how the value reads (cm = metres shown in cm, m2, deg, times, count);
# off: the value that turns the step off (shown as "off"); all: a value that keeps everything ("keep all");
# group: walls / outside / cleanup / labels; key: shown without "More settings"; help: a key of ui/help.py HELP;
# flag: fp_roomscan's option. Labels are English here and translated where they're shown
# (scripts/i18n_extract.py collects them).
REFINE_DEFAULTS: dict[str, dict] = {
    "refine": dict(default=1, kind="bool", label="Clean up the scan", group="cleanup", help="refine_refine"),
    "walls": dict(default="panels", kind="choice", label="Walls", group="walls", key=True, help="refine_walls",
                  choices=(("off", "Keep scan"), ("snap", "Snap"), ("panels", "Flat panels"))),
    "walked": dict(default=0.40, min=0, max=0.8, step=0.05, unit="cm", off=0, label="Where you walked",
                   group="cleanup", key=True, help="refine_walked"),
    "smooth": dict(default=8, min=0, max=15, step=1, unit="times", off=0, label="Smoothing", group="cleanup",
                   key=True, help="refine_smooth"),
    "island": dict(default=0.20, min=0, max=1, step=0.01, unit="m2", off=0, label="Remove pieces smaller than",
                   group="cleanup", key=True, help="refine_island"),
    "clip": dict(default=0.05, min=0, max=1, step=0.01, unit="cm", all=1, label="Keep past walls",
                 group="outside", key=True, help="refine_clip"),
    "clip_open": dict(default=0.10, min=0, max=2, step=0.05, unit="cm", all=2, label="Extra past unseen edges",
                      group="outside", help="refine_clip_open"),
    "band": dict(default=0.35, min=0.05, max=0.6, step=0.01, unit="cm", label="Behind a surface", group="walls",
                 help="refine_band"),
    "band_in": dict(default=0.12, min=0.02, max=0.6, step=0.01, unit="cm", label="In front of a wall",
                    group="walls", help="refine_band_in"),
    "floor_in": dict(default=0.08, min=0.02, max=0.4, step=0.01, unit="cm", label="Above the floor", group="walls",
                     help="refine_floor_in"),
    "ceil_in": dict(default=0.25, min=0.02, max=0.6, step=0.01, unit="cm", label="Below the ceiling",
                    group="walls", help="refine_ceil_in"),
    "top_strip": dict(default=0.5, min=0, max=1, step=0.05, unit="cm", off=0, label="Top of walls", group="walls",
                      help="refine_top_strip"),
    "facing_deg": dict(default=60, min=10, max=80, step=1, unit="deg", label="Facing within", group="walls",
                       help="refine_facing_deg"),
    "near": dict(default=0.03, min=0, max=0.6, step=0.01, unit="cm", off=0, label="Also clear near walls",
                 group="walls", help="refine_near"),
    "tubes": dict(default=3.0, min=0, max=6, step=0.1, unit="times", off=0, label="Remove thin floating pieces",
                  group="cleanup", help="refine_tubes"),
    "tube_thick": dict(default=0.15, min=0.04, max=0.4, step=0.01, unit="cm", label="…no thicker than",
                       group="cleanup", help="refine_tube_thick"),
    "tops": dict(default=0.10, min=0, max=0.2, step=0.01, unit="cm", off=0, label="Flatten furniture tops",
                 group="cleanup", help="refine_tops"),
    "protect": dict(default=0, kind="bool", label="Never remove scan inside furniture", group="cleanup",
                    help="refine_protect"),
    "label_rules": dict(default=1, kind="bool", label="Check furniture labels", group="labels",
                        help="refine_label_rules"),
}
for _key, _meta in REFINE_DEFAULTS.items():
    _meta.setdefault("kind", "number")
    _meta.setdefault("key", False)
    _meta["flag"] = "--" + _key.replace("_", "-")
REFINE_GROUPS = (("walls", "Walls, floor and ceiling"), ("outside", "Outside the room"),
                 ("cleanup", "Floating pieces and furniture"),
                 ("labels", "Label rules"))
INT_SETTINGS = ("refine", "smooth", "protect", "label_rules")


def refine_defaults() -> dict:
    return {k: m["default"] for k, m in REFINE_DEFAULTS.items()}


def normalize_refine(settings: dict | None) -> dict:
    """Only the settings that differ from the defaults, each checked, snapped to its step and clamped to its slider
    (unknown keys raise ValueError)."""
    out = {}
    for key, value in (settings or {}).items():
        meta = REFINE_DEFAULTS.get(key)
        if meta is None:
            raise ValueError(f"unknown clean-up setting: {key}")
        if meta["kind"] == "choice":
            if value not in [c for c, _ in meta["choices"]]:
                raise ValueError(f"{key}: expected one of " + ", ".join(c for c, _ in meta["choices"]))
        elif meta["kind"] == "bool":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "on", "yes")
            value = 1 if value else 0
        else:
            value = float(value)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"{key}: not a number")
            value = min(meta["max"], max(meta["min"], value))
            value = round(round((value - meta["min"]) / meta["step"]) * meta["step"] + meta["min"], 4)
            value = int(round(value)) if key in INT_SETTINGS else value
        if value != meta["default"]:
            out[key] = value
    return out


def current_refine(status: dict | None) -> dict:
    """Every clean-up setting as the Frame has it saved (room_status' refine = the non-defaults)."""
    return {**refine_defaults(), **normalize_refine((status or {}).get("refine") or {})}


def refine(frame, settings: dict | None = None, erase: list | None = None, keep_edits: bool = True) -> dict:
    """Clean up the saved room's scan again on the Frame (fp_roomscan --refine-only from its raw files) and save
    `settings` there for the next scans (None = keep the saved ones; {} = the defaults). erase: [[x, y, z, r], ...]
    spheres of scan to remove. keep_edits=False drops the player's own furniture / doors (back to the scan's).
    Returns room_status with the clean-up's report, label decisions and suggestions (+ summary: its JSON output)."""
    ensure_scanner(frame)
    args: dict = {"settings": None if settings is None else normalize_refine(settings), "keep_edits": keep_edits}
    if erase:
        args["erase"] = [[float(v) for v in spot] for spot in erase]
    result = frame.agent("room_refine", **args)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def put_room(frame, room, mesh: bytes | None = None) -> dict:
    """Save an edited copy of the stored room (furniture boxes, doors and windows from the Room tab): the Frame keeps
    the scan's raw files and remembers the edits, so a new clean-up keeps them. `mesh` = the room's mesh sidecar
    (fetched from the Frame when not given)."""
    if room.mesh and mesh is None:
        remote = f"{frame.home}/.local/share/frameport/rooms/{room.universe}/{room.mesh.filename}"
        with frame.sftp.open(remote, "rb") as f:
            mesh = f.read(64 * 1024 * 1024)
    if room.mesh:
        m = read_mesh(mesh)
        if (m.vertex_count, m.index_count) != (room.mesh.vertex_count, room.mesh.index_count):
            raise ValueError("The room changed on the Frame; refresh and edit again")
    staging = user_data_dir() / "rooms"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="edit-", dir=staging) as local, _upload_dir(frame) as remote:
        path = Path(local) / "room.txt"
        path.write_text(dumps(room), encoding="utf-8")
        frame.put(path, f"{remote}/room.txt", resume=False)
        if room.mesh:
            (Path(local) / room.mesh.filename).write_bytes(mesh)
            frame.put(Path(local) / room.mesh.filename, f"{remote}/{room.mesh.filename}", resume=False)
        return frame.agent("room_put", path=f"{remote}/room.txt", edited=True)


def show(frame, path: Path) -> dict:
    from ..mr.room import summary

    st = status(frame)
    if not st.get("room_text"):
        raise ValueError("No room is stored on the Frame")
    room = parse(st["room_text"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(preview_svg(room), encoding="utf-8")
    return summary(room)
