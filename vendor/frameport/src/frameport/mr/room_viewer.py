"""The Room tab's full 3D view: a tiny web server on 127.0.0.1 for the WebGL page in viewer_page.py, opened in the
user's browser (Flet has no 3D widget). No Flet.

Every path starts with a random per-session token (`/<token>/`, `/<token>/room.json`, `/<token>/mesh.bin`,
`/<token>/version`): other programs on this PC can't guess it, and nothing listens beyond this PC. The mesh is the
room file's FPRM bytes, served unchanged. Stopped when the Frame disconnects or the window closes."""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .room import Room, parse, summary
from .view3d import box_corners
from .viewer_page import VIEWER_HTML


def room_json(room: Room, has_mesh: bool, version: int = 0) -> dict:
    """What the page draws (the furniture's corners computed here, so the yaw convention lives in one place)."""
    return {
        "version": version, "universe": str(room.universe), "source": room.source, "time": room.time,
        "floor": room.floor, "ceiling": room.ceiling, "outline": [list(p) for p in room.outline],
        "walls": [{"x0": w.x0, "z0": w.z0, "x1": w.x1, "z1": w.z1, "y0": w.y0, "y1": w.y1, "label": w.label}
                  for w in room.walls],
        "volumes": [{"label": v.label, "cx": v.cx, "cz": v.cz, "top_y": v.top_y, "yaw_deg": v.yaw_deg, "w": v.w,
                     "d": v.d, "h": v.h, "corners": [list(c) for c in box_corners(v.cx, v.cz, v.yaw_deg, v.w, v.d)]}
                    for v in room.volumes],
        "planes": [{"label": p.label, "pos": [p.px, p.py, p.pz], "quat": [p.qx, p.qy, p.qz, p.qw], "w": p.w,
                    "h": p.h} for p in room.planes],
        "summary": summary(room), "has_mesh": has_mesh,
    }


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):  # nothing from the network, no frames
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        viewer = self.server.viewer
        path = self.path.split("?", 1)[0]
        prefix = f"/{viewer.token}/"
        if not path.startswith(prefix):
            self._send(404, b"not found", "text/plain")
            return
        name = path[len(prefix):]
        room_text, mesh, version = viewer.snapshot()
        if name in ("", "index.html"):
            self._send(200, VIEWER_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif name == "version":
            self._send(200, str(version).encode(), "text/plain")
        elif name == "room.json":
            if room_text is None:
                self._send(404, b"no room", "text/plain")
                return
            data = room_json(parse(room_text), mesh is not None, version)
            self._send(200, json.dumps(data).encode(), "application/json")
        elif name == "mesh.bin":
            if mesh is None:
                self._send(404, b"no mesh", "text/plain")
            else:
                self._send(200, mesh, "application/octet-stream")
        else:
            self._send(404, b"not found", "text/plain")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    viewer: RoomViewerServer


class RoomViewerServer:
    """start() once, set_room() whenever the room changes (any thread), stop() at disconnect / window close."""

    def __init__(self, host: str = "127.0.0.1"):
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self._room: str | None = None
        self._mesh: bytes | None = None
        self._version = 0
        self.server = _Server((host, 0), _Handler)
        self.server.viewer = self
        self._thread: threading.Thread | None = None
        self.stopped = False

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/{self.token}/"

    def set_room(self, room_text: str | None, mesh: bytes | None = None) -> None:
        if room_text is not None:
            parse(room_text)  # never serve a room the page can't read
        with self._lock:
            if (room_text, mesh) != (self._room, self._mesh):
                self._room, self._mesh = room_text, mesh
                self._version += 1

    def snapshot(self) -> tuple[str | None, bytes | None, int]:
        with self._lock:
            return self._room, self._mesh, self._version

    def start(self) -> RoomViewerServer:
        if self._thread is None:
            self._thread = threading.Thread(target=self.server.serve_forever, name="room-viewer", daemon=True)
            self._thread.start()
        return self

    @property
    def running(self) -> bool:
        return self._thread is not None and not self.stopped

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        if self._thread is not None:
            self.server.shutdown()
        self.server.server_close()
