"""FramePort room format v1, in SteamVR STAGE coordinates (metres, +Y up).

Unknown text records are ignored so a newer scanner can work with an older app.
Known records are validated before upload; mesh paths never escape the room folder.
"""
from __future__ import annotations

import math
import re
import struct
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from html import escape


def _id() -> str:
    return uuid.uuid4().hex


@dataclass
class Wall:
    uuid: str
    x0: float
    z0: float
    x1: float
    z1: float
    y0: float = 0
    y1: float = 2.5
    label: str = "WALL_FACE"  # INVISIBLE_WALL_FACE: an edge the scanner closed without seeing a wall


WALL_LABELS = ("WALL_FACE", "INVISIBLE_WALL_FACE")


@dataclass
class Volume:
    uuid: str
    label: str
    cx: float
    cz: float
    top_y: float
    yaw_deg: float
    w: float
    d: float
    h: float


@dataclass
class Plane:
    uuid: str
    label: str
    px: float
    py: float
    pz: float
    qx: float
    qy: float
    qz: float
    qw: float
    w: float
    h: float


@dataclass
class Mesh:
    uuid: str = field(default_factory=_id)
    filename: str = "room-mesh.bin"
    vertex_count: int = 0
    index_count: int = 0
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)

    def __post_init__(self):
        if self.vertices:
            self.vertex_count = len(self.vertices)
        if self.indices:
            self.index_count = len(self.indices)


@dataclass
class Room:
    universe: int = 0
    source: str = "manual"
    time: int = field(default_factory=lambda: int(time.time()))
    uuid: str = field(default_factory=_id)
    floor_uuid: str = field(default_factory=_id)
    ceiling_uuid: str = field(default_factory=_id)
    floor: float = 0
    ceiling: float = 2.5
    outline: list[tuple[float, float]] = field(default_factory=list)
    walls: list[Wall] = field(default_factory=list)
    volumes: list[Volume] = field(default_factory=list)
    planes: list[Plane] = field(default_factory=list)
    mesh: Mesh | None = None
    play_area: list[tuple[float, float]] = field(default_factory=list)


def _area(points) -> float:
    return sum(x * b - a * z for (x, z), (a, b) in zip(points, points[1:] + points[:1], strict=True)) / 2


def _uuid(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("UUID must be 32 lowercase hex digits")
    return value


def _numbers(values):
    result = [float(v) for v in values]
    if not all(math.isfinite(v) for v in result):
        raise ValueError("coordinates must be finite")
    return result


def _finish(room: Room) -> Room:
    if not room.outline and room.walls:
        xs = [x for w in room.walls for x in (w.x0, w.x1)]
        zs = [z for w in room.walls for z in (w.z0, w.z1)]
        room.outline = [(min(xs), min(zs)), (max(xs), min(zs)),
                        (max(xs), max(zs)), (min(xs), max(zs))]
    if len(room.outline) < 3 or abs(_area(room.outline)) < 1e-8:
        raise ValueError("room needs an outline with nonzero area or walls")
    if _area(room.outline) < 0:
        room.outline.reverse()
    if room.ceiling <= room.floor:
        raise ValueError("ceiling must be above floor")
    if not room.walls:
        room.walls = [Wall(_id(), *a, *b, room.floor, room.ceiling)
                      for a, b in zip(room.outline, room.outline[1:] + room.outline[:1], strict=True)]
    edges = list(zip(room.outline, room.outline[1:] + room.outline[:1], strict=True))
    for wall in room.walls:
        if wall.y1 <= wall.y0 or (wall.x0, wall.z0) == (wall.x1, wall.z1):
            raise ValueError("wall must have positive height and length")
        if ((wall.x1, wall.z1), (wall.x0, wall.z0)) in edges:
            wall.x0, wall.x1 = wall.x1, wall.x0
            wall.z0, wall.z1 = wall.z1, wall.z0
    ids = [room.uuid, room.floor_uuid, room.ceiling_uuid]
    ids += [v.uuid for v in [*room.walls, *room.volumes, *room.planes]]
    if room.mesh:
        ids.append(room.mesh.uuid)
    if len(ids) != len(set(ids)):
        raise ValueError("entity UUIDs must be unique")
    return room


def parse(text: str) -> Room:
    """Read v1 text, tolerating comments, blank lines and future record types."""
    lines = [line.split("#", 1)[0].split() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines or lines[0] != ["frameport-room", "1"]:
        raise ValueError("expected frameport-room 1")
    room, ceiling_seen = Room(), False
    for parts in lines[1:]:
        kind, values = parts[0], parts[1:]
        try:
            if kind in ("universe", "time"):
                if len(values) != 1 or not values[0].isdigit():
                    raise ValueError("expected a nonnegative integer")
                n = int(values[0])
                if kind == "universe" and n >= 2**64:
                    raise ValueError("universe exceeds u64")
                setattr(room, kind, n)
            elif kind == "source":
                if len(values) != 1 or values[0] not in ("scan", "boundary", "box", "manual"):
                    raise ValueError("invalid source")
                room.source = values[0]
            elif kind == "room":
                if len(values) != 1:
                    raise ValueError("expected UUID")
                room.uuid = _uuid(values[0])
            elif kind in ("floor", "ceiling"):
                ident, height = values
                setattr(room, kind + "_uuid", _uuid(ident))
                setattr(room, kind, _numbers([height])[0])
                ceiling_seen |= kind == "ceiling"
            elif kind == "outline":
                n = int(values[0])
                coords = _numbers(values[1:])
                if n < 3 or len(coords) != n * 2:
                    raise ValueError("outline count does not match coordinates")
                room.outline = list(zip(coords[::2], coords[1::2], strict=True))
            elif kind == "wall":
                if len(values) not in (7, 8) or (len(values) == 8 and values[7] not in WALL_LABELS):
                    raise ValueError("expected UUID, 6 coordinates and an optional wall label")
                room.walls.append(Wall(_uuid(values[0]), *_numbers(values[1:7]), *values[7:]))
            elif kind in ("volume", "plane"):
                count = 9 if kind == "volume" else 11
                if len(values) != count or not re.fullmatch(r"[A-Z][A-Z0-9_]*", values[1]):
                    raise ValueError("invalid label or coordinate count")
                obj = (Volume if kind == "volume" else Plane)(_uuid(values[0]), values[1], *_numbers(values[2:]))
                if min(obj.w, obj.h, obj.d if kind == "volume" else obj.w) <= 0:
                    raise ValueError("dimensions must be positive")
                if kind == "plane" and sum(q*q for q in (obj.qx, obj.qy, obj.qz, obj.qw)) < 1e-12:
                    raise ValueError("plane quaternion is zero")
                (room.volumes if kind == "volume" else room.planes).append(obj)
            elif kind == "mesh":
                ident, filename, nv, ni = values
                if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", filename) or filename in ("room.txt", "scan.log"):
                    raise ValueError("mesh must be a sidecar filename")
                nv, ni = int(nv), int(ni)
                if nv < 0 or ni < 0 or ni % 3 or ni > 600000:
                    raise ValueError("invalid mesh counts")
                room.mesh = Mesh(_uuid(ident), filename, nv, ni)
        except (ValueError, IndexError, TypeError) as exc:
            raise ValueError(f"{kind}: {exc}") from exc
    if not ceiling_seen:
        room.ceiling = room.floor + 2.5
    return _finish(room)


def dumps(room: Room) -> str:
    """Write canonical text; validate programmatically constructed rooms too."""
    _finish(room)

    def row(*values):
        return " ".join(format(v, ".9g") if isinstance(v, float) else str(v) for v in values)

    rows = ["frameport-room 1", row("universe", room.universe), row("source", room.source), row("time", room.time),
            row("room", room.uuid), row("floor", room.floor_uuid, room.floor),
            row("ceiling", room.ceiling_uuid, room.ceiling),
            row("outline", len(room.outline), *(v for p in room.outline for v in p))]
    for w in room.walls:  # the label only when it isn't the default (older readers know 7 fields)
        rows.append(row("wall", w.uuid, w.x0, w.z0, w.x1, w.z1, w.y0, w.y1,
                        *([w.label] if w.label != "WALL_FACE" else [])))
    for kind, objects in (("volume", room.volumes), ("plane", room.planes)):
        rows.extend(row(kind, *vars(obj).values()) for obj in objects)
    if room.mesh:
        m = room.mesh
        rows.append(row("mesh", m.uuid, m.filename, m.vertex_count, m.index_count))
    text = "\n".join(rows) + "\n"
    parse(text)
    return text


def read_mesh(data: bytes) -> Mesh:
    if len(data) < 16:
        raise ValueError("truncated mesh header")
    magic, version, nv, ni = struct.unpack_from("<4sIII", data)
    if magic != b"FPRM" or version != 1:
        raise ValueError("expected FPRM version 1")
    if ni % 3 or ni > 600000 or len(data) != 16 + nv * 12 + ni * 4:
        raise ValueError("invalid mesh length or triangle count")
    vertices = list(struct.iter_unpack("<fff", data[16:16 + nv * 12]))
    indices = [v[0] for v in struct.iter_unpack("<I", data[16 + nv * 12:])]
    if any(not math.isfinite(x) for p in vertices for x in p) or any(i >= nv for i in indices):
        raise ValueError("invalid mesh coordinate or index")
    return Mesh(vertex_count=nv, index_count=ni, vertices=vertices, indices=indices)


def write_mesh(mesh: Mesh) -> bytes:
    data = struct.pack("<4sIII", b"FPRM", 1, len(mesh.vertices), len(mesh.indices))
    data += b"".join(struct.pack("<fff", *p) for p in mesh.vertices)
    data += b"".join(struct.pack("<I", i) for i in mesh.indices)
    read_mesh(data)
    return data


def from_boundary(outline_xz, height: float = 2.5) -> Room:
    coords = [tuple(_numbers(p)) for p in outline_xz]
    if any(len(p) != 2 for p in coords) or not math.isfinite(height) or height <= 0:
        raise ValueError("invalid boundary or height")
    return _finish(Room(source="boundary", ceiling=height, outline=coords))


def box_room(width: float, depth: float, height: float = 2.5) -> Room:
    if not all(math.isfinite(v) and v > 0 for v in (width, depth, height)):
        raise ValueError("room dimensions must be positive and finite")
    w, d = width / 2, depth / 2
    room = from_boundary([(-w, -d), (w, -d), (w, d), (-w, d)], height)
    room.source = "box"
    return room


def summary(room: Room) -> dict:
    return {"walls": len(room.walls), "volumes": dict(Counter(v.label for v in room.volumes)),
            "planes": len(room.planes), "floor_area": abs(_area(room.outline)),
            "ceiling_height": room.ceiling - room.floor}


# ------------------------------------------------------------------------------------------ editing helpers
VOLUME_LABELS = ("TABLE", "COUCH", "BED", "STORAGE", "SCREEN", "LAMP", "PLANT", "OTHER")
PLANE_LABELS = ("DOOR_FRAME", "WINDOW_FRAME", "WALL_ART")
PLANE_INSET = 0.01  # doors/windows sit this far in front of their wall (into the room)


def new_id() -> str:
    return _id()


def nearest_wall(room: Room, x: float, z: float) -> dict | None:
    """The wall closest to (x, z): index i, distance d, t (0..1 along it), length L, direction (ux, uz) and the
    closest point (px, pz)."""
    best = None
    for i, w in enumerate(room.walls):
        abx, abz = w.x1 - w.x0, w.z1 - w.z0
        length = math.hypot(abx, abz)
        if length < 1e-9:
            continue
        t = max(0.0, min(1.0, ((x - w.x0) * abx + (z - w.z0) * abz) / (length * length)))
        px, pz = w.x0 + t * abx, w.z0 + t * abz
        d = math.hypot(x - px, z - pz)
        if best is None or d < best["d"]:
            best = dict(i=i, d=d, t=t, L=length, ux=abx / length, uz=abz / length, px=px, pz=pz)
    return best


def plane_on_wall(room: Room, wall: int, along: float, bottom: float, w: float, h: float,
                  label: str = "DOOR_FRAME", uuid: str | None = None) -> Plane:
    """A door / window / wall-art plane on a wall (the cleanup page's planeGeom): `along` = 0..1 of the wall's length
    for its middle (kept fully on the wall), `bottom` = metres above the floor. Meta's convention: bounds in local XY,
    local +Z = the normal into the room."""
    wl = room.walls[wall]
    dx, dz = wl.x1 - wl.x0, wl.z1 - wl.z0
    length = math.hypot(dx, dz)
    ux, uz = dx / length, dz / length
    nx, nz = -uz, ux
    a = max(min(w / 2, length / 2), min(length - w / 2, along * length))
    th = math.atan2(-uz, ux)  # about +Y: +X becomes the wall's direction, +Z its inward normal
    return Plane(uuid or _id(), label, wl.x0 + ux * a + nx * PLANE_INSET, room.floor + bottom + h / 2,
                 wl.z0 + uz * a + nz * PLANE_INSET, 0.0, math.sin(th / 2), 0.0, math.cos(th / 2), w, h)


def plane_place(room: Room, plane: Plane) -> dict:
    """Where a plane sits for the editor: wall (index of the nearest wall), along (0..1), bottom (m above the floor)."""
    near = nearest_wall(room, plane.px, plane.pz)
    if near is None:
        return dict(wall=0, along=0.5, bottom=max(0.0, plane.py - plane.h / 2 - room.floor))
    return dict(wall=near["i"], along=near["t"], bottom=max(0.0, plane.py - plane.h / 2 - room.floor))


def plane_corners(plane: Plane) -> list[tuple[float, float, float]]:
    """A plane's four corners (bottom left, bottom right, top right, top left, seen from the room)."""
    th = 2 * math.atan2(plane.qy, plane.qw)  # (planes are upright: a turn about +Y)
    ux, uz = math.cos(th), -math.sin(th)
    hw, hh = plane.w / 2, plane.h / 2
    x, y, z = plane.px, plane.py, plane.pz
    return [(x - ux * hw, y - hh, z - uz * hw), (x + ux * hw, y - hh, z + uz * hw),
            (x + ux * hw, y + hh, z + uz * hw), (x - ux * hw, y + hh, z - uz * hw)]


def default_plane(room: Room, label: str) -> Plane:
    """A new door (0.9 x 2.05 m from the floor) or window (1.2 x 1.2 m, 0.9 m up) in the middle of the first wall
    the scan saw."""
    wall = next((i for i, w in enumerate(room.walls) if w.label != "INVISIBLE_WALL_FACE"), 0)
    door = label == "DOOR_FRAME"
    return plane_on_wall(room, wall, 0.5, 0 if door else 0.9, 0.9 if door else 1.2, 2.05 if door else 1.2, label)


def default_volume(room: Room) -> Volume:
    """A new box: a 1.2 x 0.6 m table in the middle of the room."""
    xs = [x for x, _ in room.outline]
    zs = [z for _, z in room.outline]
    return Volume(_id(), "TABLE", (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2, room.floor + 0.75, 0.0,
                  1.2, 0.6, 0.75)


def accept_suggestion(room: Room, suggestion: dict, kind: str) -> Volume | Plane:
    """A scanner suggestion (a tall thin slab by a wall) as a door on that wall or as a closet against it (the
    cleanup page's acceptSuggestion). Added to the room and returned."""
    box = suggestion.get("box") or {}
    cx, cz = float(box.get("cx", 0)), float(box.get("cz", 0))
    w, d = float(box.get("w", 0.9)), float(box.get("d", 0.1))
    h = float(box.get("h", 2.0))
    top = float(box.get("top_y", room.floor + h))
    width = max(w, d)
    near = nearest_wall(room, cx, cz)
    wall, along = (near["i"], near["t"]) if near else (0, 0.5)
    if isinstance(suggestion.get("wall"), int) and 0 <= suggestion["wall"] < len(room.walls):
        wall = suggestion["wall"]
        along = float(suggestion.get("along", along))
    if kind == "door":
        plane = plane_on_wall(room, wall, along, 0.0, min(max(width, 0.8), 1.2), min(max(h, 1.95), 2.2), "DOOR_FRAME")
        room.planes.append(plane)
        return plane
    wl = room.walls[wall]
    length = math.hypot(wl.x1 - wl.x0, wl.z1 - wl.z0)
    ux, uz = (wl.x1 - wl.x0) / length, (wl.z1 - wl.z0) / length
    px, pz = wl.x0 + ux * along * length, wl.z0 + uz * along * length
    nx, nz, depth = -uz, ux, 0.6  # against the wall, inside the room
    height = min(top - room.floor, 2.4)
    vol = Volume(_id(), "STORAGE", px + nx * depth / 2, pz + nz * depth / 2, room.floor + height,
                 math.degrees(math.atan2(-uz, ux)), max(width, 0.6), depth, height)
    room.volumes.append(vol)
    return vol


def preview_svg(room: Room) -> str:
    """Self-contained top-down preview; text labels are escaped, never interpreted as markup."""
    points = room.outline + room.play_area
    xmin, xmax = min(x for x, _ in points), max(x for x, _ in points)
    zmin, zmax = min(z for _, z in points), max(z for _, z in points)
    scale = 440 / max(xmax-xmin, zmax-zmin, .1)
    def point(x, z):  # seen from above: +X right, forward (-Z) at the top (+Z at the top would mirror the room)
        return f"{30+(x-xmin)*scale:.2f},{30+(z-zmin)*scale:.2f}"
    def polygon(poly):
        return " ".join(point(x, z) for x, z in poly)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 500" role="img">',
           '<title>Room floor plan</title>',
           f'<polygon points="{polygon(room.outline)}" fill="#17232f" stroke="#f49a42" stroke-width="3"/>']
    for wall in room.walls:
        x0, y0 = point(wall.x0, wall.z0).split(",")
        x1, y1 = point(wall.x1, wall.z1).split(",")
        out.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="#f49a42" stroke-width="3"/>')
    for v in room.volumes:
        angle = math.radians(v.yaw_deg)
        c, s = math.cos(angle), math.sin(angle)
        corners = [(v.cx+c*x+s*z, v.cz-s*x+c*z)
                   for x, z in [(-v.w/2, -v.d/2), (v.w/2, -v.d/2), (v.w/2, v.d/2), (-v.w/2, v.d/2)]]
        x, y = point(v.cx, v.cz).split(",")
        out += [f'<polygon points="{polygon(corners)}" fill="#275772" stroke="#65bcec"/>',
                f'<text x="{x}" y="{y}" text-anchor="middle" fill="#ffffff" font-size="12">{escape(v.label)}</text>']
    if room.play_area:
        out.append(f'<polygon points="{polygon(room.play_area)}" fill="none" stroke="#65bcec" stroke-dasharray="6 4"/>')
    return "\n".join(out + ["</svg>"])
