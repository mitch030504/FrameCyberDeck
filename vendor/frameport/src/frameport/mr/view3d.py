"""A small 3D model of a room for drawing on a 2D canvas, no Flet: geometry prepared once per room, then a camera that
projects it. Pure Python, kept cheap: the mesh is reduced to a few thousand edges ahead of time, so a redraw only
projects about a thousand points.

Coordinates are the room file's STAGE space (metres, +Y up, -Z = the play area's forward). Screen coordinates grow
right and down. Walls and mesh triangles face into the room, so the walls nearest to the camera are left out (a
"dollhouse" view from outside); from inside the room every wall shows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .room import Mesh, Room

REST_BUDGET = 4000  # mesh edges drawn when the view is still
DRAG_BUDGET = 1500  # while the view moves (a smaller patch per redraw)
GRID_STEPS = (0.08, 0.12, 0.16, 0.2, 0.25, 0.32, 0.4, 0.5, 0.64, 0.8, 1.0, 1.28)  # metres per cluster cell


def box_corners(cx: float, cz: float, yaw_deg: float, w: float, d: float) -> list[tuple[float, float]]:
    """A volume's footprint corners (x, z), in the order and yaw convention of room.preview_svg and scene_room.c:
    local (-w/2, -d/2), (w/2, -d/2), (w/2, d/2), (-w/2, d/2)."""
    a = math.radians(yaw_deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + c * x + s * z, cz - s * x + c * z)
            for x, z in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))]


@dataclass
class Face:
    points: list[tuple[float, float, float]]
    normal: tuple[float, float, float]  # facing direction; the face is drawn when the camera is on this side
    kind: str = "wall"                  # wall | hidden_wall | box
    label: str = ""
    index: int = -1                     # a box side: the volume's index in the room


@dataclass
class MeshLod:
    """A reduced mesh: unique vertices, triangles with their normals and the edges between them (each edge with the
    triangles it belongs to, so it's drawn when any of them faces the camera)."""
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    centers: list[tuple[float, float, float]] = field(default_factory=list)
    edges: list[tuple[int, int, tuple[int, ...]]] = field(default_factory=list)
    cell: float = 0.0


@dataclass
class Scene:
    faces: list[Face] = field(default_factory=list)           # walls and furniture box sides
    floor: list[tuple[float, float, float]] = field(default_factory=list)
    box_edges: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = field(default_factory=list)
    grid: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = field(default_factory=list)
    lods: dict[str, MeshLod] = field(default_factory=dict)   # "rest", "drag"
    lo: tuple[float, float, float] = (-1.0, 0.0, -1.0)
    hi: tuple[float, float, float] = (1.0, 2.5, 1.0)
    triangles: int = 0                                         # of the full mesh
    planes: list[list[tuple[float, float, float]]] = field(default_factory=list)  # doors / windows (corners)
    selected: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ vector helpers
def _sub(a, b):
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


# ------------------------------------------------------------------------------------------------ mesh reduction
def _cluster(vertices, tris, cell: float):
    """Vertex clustering: every vertex moves to the mean of its grid cell; triangles that collapse go away."""
    ids: dict[tuple[int, int, int], int] = {}
    sums: list[list[float]] = []
    remap = []
    inv = 1.0 / cell
    for x, y, z in vertices:
        key = (math.floor(x * inv), math.floor(y * inv), math.floor(z * inv))
        i = ids.get(key)
        if i is None:
            i = ids[key] = len(sums)
            sums.append([0.0, 0.0, 0.0, 0])
        s = sums[i]
        s[0] += x
        s[1] += y
        s[2] += z
        s[3] += 1
        remap.append(i)
    out_v = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) for s in sums]
    seen, out_t = set(), []
    for a, b, c in tris:
        a, b, c = remap[a], remap[b], remap[c]
        if a == b or b == c or a == c:
            continue
        key = tuple(sorted((a, b, c)))
        if key in seen:
            continue
        seen.add(key)
        out_t.append((a, b, c))
    return out_v, out_t


def _edges(tris):
    adj: dict[tuple[int, int], list[int]] = {}
    for t, (a, b, c) in enumerate(tris):
        for u, v in ((a, b), (b, c), (c, a)):
            adj.setdefault((u, v) if u < v else (v, u), []).append(t)
    return adj


def _lod(vertices, tris, adj, cell: float, budget: int) -> MeshLod:
    lod = MeshLod(cell=cell)
    used = sorted({i for t in tris for i in t})
    index = {old: new for new, old in enumerate(used)}
    lod.vertices = [vertices[i] for i in used]
    keep_tri = {}
    for t, (a, b, c) in enumerate(tris):
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        n = _norm(_cross(_sub(pb, pa), _sub(pc, pa)))
        if n == (0.0, 0.0, 0.0):
            continue
        keep_tri[t] = len(lod.normals)
        lod.normals.append(n)
        lod.centers.append(((pa[0] + pb[0] + pc[0]) / 3, (pa[1] + pb[1] + pc[1]) / 3, (pa[2] + pb[2] + pc[2]) / 3))
    edges = []
    for (u, v), ts in adj.items():
        owners = tuple(keep_tri[t] for t in ts if t in keep_tri)
        if owners:
            edges.append((index[u], index[v], owners))
    if len(edges) > budget:  # the coarsest grid still has too many: keep the longest, most upright ones
        def weight(e):
            p, q = lod.vertices[e[0]], lod.vertices[e[1]]
            d = _sub(q, p)
            return _dot(d, d) * (1.0 + abs(d[1]) / (math.sqrt(_dot(d, d)) or 1.0))
        edges.sort(key=weight, reverse=True)
        edges = edges[:budget]
    lod.edges = edges
    return lod


def reduce_mesh(mesh: Mesh, budgets: dict[str, int]) -> dict[str, MeshLod]:
    """One MeshLod per budget (name -> most edges): the finest cluster grid whose edges fit the budget."""
    verts = mesh.vertices
    idx = mesh.indices
    tris = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx) - 2, 3)]
    if not tris:
        return {}
    out: dict[str, MeshLod] = {}
    want = sorted(budgets.items(), key=lambda kv: -kv[1])  # biggest budget first: grids only get coarser
    cur_v, cur_t = _cluster(verts, tris, GRID_STEPS[0])
    step = 0
    for name, budget in want:
        while True:
            adj = _edges(cur_t)
            if len(adj) <= budget or step == len(GRID_STEPS) - 1:
                out[name] = _lod(cur_v, cur_t, adj, GRID_STEPS[step], budget)
                break
            step += 1
            # cluster the already reduced vertices again (cheap: far fewer than the mesh has)
            cur_v, cur_t = _cluster(cur_v, cur_t, GRID_STEPS[step])
    return out


# ------------------------------------------------------------------------------------------------ scene
def prepare(room: Room, mesh: Mesh | None = None, budget: int = REST_BUDGET,
            drag_budget: int = DRAG_BUDGET) -> Scene:
    """Everything the canvas draws, built once per room (call it on a background thread: a full scan's mesh takes
    about a second)."""
    sc = Scene()
    y0, y1 = room.floor, room.ceiling
    for w in room.walls:
        dx, dz = w.x1 - w.x0, w.z1 - w.z0
        n = _norm((-dz, 0.0, dx))  # into the room (docs/mr/ROOM_FORMAT.md)
        sc.faces.append(Face([(w.x0, w.y0, w.z0), (w.x1, w.y0, w.z1), (w.x1, w.y1, w.z1), (w.x0, w.y1, w.z0)], n,
                             "hidden_wall" if w.label == "INVISIBLE_WALL_FACE" else "wall", w.label))
    sc.floor = [(x, y0, z) for x, z in room.outline]
    _add_furniture(sc, room)
    xs = [x for x, _ in room.outline]
    zs = [z for _, z in room.outline]
    lo, hi = [min(xs), y0, min(zs)], [max(xs), y1, max(zs)]
    if mesh is not None and mesh.vertices:
        sc.triangles = len(mesh.indices) // 3
        sc.lods = reduce_mesh(mesh, {"rest": budget, "drag": drag_budget})
        rest = sc.lods.get("rest")
        if rest is not None and rest.vertices:  # 2nd..98th percentile: a scan's stray far-off bits don't count
            for k in range(3):
                vals = sorted(p[k] for p in rest.vertices)
                lo[k] = min(lo[k], vals[int(len(vals) * 0.02)])
                hi[k] = max(hi[k], vals[min(len(vals) - 1, int(len(vals) * 0.98))])
    sc.lo, sc.hi = tuple(lo), tuple(hi)
    for gx in range(math.floor(lo[0]), math.ceil(hi[0]) + 1):  # 1 m floor grid
        sc.grid.append(((gx, y0, math.floor(lo[2])), (gx, y0, math.ceil(hi[2]))))
    for gz in range(math.floor(lo[2]), math.ceil(hi[2]) + 1):
        sc.grid.append(((math.floor(lo[0]), y0, gz), (math.ceil(hi[0]), y0, gz)))
    return sc


def _box_quads(v):
    foot = box_corners(v.cx, v.cz, v.yaw_deg, v.w, v.d)
    top, bottom = v.top_y, v.top_y - v.h
    lo_pts = [(x, bottom, z) for x, z in foot]
    hi_pts = [(x, top, z) for x, z in foot]
    sides = [[lo_pts[i], lo_pts[(i + 1) % 4], hi_pts[(i + 1) % 4], hi_pts[i]] for i in range(4)]
    edges = []
    for i in range(4):
        j = (i + 1) % 4
        edges += [(lo_pts[i], lo_pts[j]), (hi_pts[i], hi_pts[j]), (lo_pts[i], hi_pts[i])]
    return sides + [hi_pts, lo_pts], edges, (v.cx, (top + bottom) / 2, v.cz)


def _add_furniture(sc: Scene, room: Room, selected: tuple[str, int] | None = None) -> None:
    from .room import plane_corners

    for n, v in enumerate(room.volumes):
        quads, edges, center = _box_quads(v)
        for quad in quads:
            mid = tuple(sum(p[k] for p in quad) / 4 for k in range(3))
            sc.faces.append(Face(quad, _norm(_sub(mid, center)), "box", v.label, n))  # outwards
        sc.box_edges += edges
        if selected == ("box", n):
            sc.selected = list(edges)
    sc.planes = [plane_corners(p) for p in room.planes]
    if selected and selected[0] == "plane" and 0 <= selected[1] < len(sc.planes):
        c = sc.planes[selected[1]]
        sc.selected = [(c[i], c[(i + 1) % 4]) for i in range(4)]


def with_furniture(scene: Scene, room: Room, selected: tuple[str, int] | None = None) -> Scene:
    """A copy of a prepared scene with the room's furniture boxes and doors / windows drawn anew (the box editor:
    cheap, the walls and the reduced mesh are shared); selected = ("box", i) / ("plane", i) is outlined."""
    sc = replace(scene, faces=[f for f in scene.faces if f.kind != "box"], box_edges=[], planes=[], selected=[])
    _add_furniture(sc, room, selected)
    return sc


def _inside(poly, x, y) -> bool:
    hit = False
    for i, (ax, ay) in enumerate(poly):
        bx, by = poly[i - 1]
        if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
            hit = not hit
    return hit


def pick(scene: Scene, cam: Camera, x: float, y: float) -> tuple[str, int] | None:
    """What's under a click at screen (x, y): ("box", volume index) / ("plane", plane index), the nearest to the
    camera, or None."""
    pr = cam.frame()
    best = None
    for face in scene.faces:
        if face.kind != "box":
            continue
        poly = pr.polygon(face.points)
        if poly and _inside(poly, x, y):
            d = pr.depth(face.points)
            if best is None or d < best[0]:
                best = (d, ("box", face.index))
    for n, corners in enumerate(scene.planes):
        poly = pr.polygon(corners)
        if poly and _inside(poly, x, y):
            d = pr.depth(corners) - 0.05  # a door in front of the box it's on
            if best is None or d < best[0]:
                best = (d, ("plane", n))
    return best[1] if best else None


# ------------------------------------------------------------------------------------------------ camera
@dataclass
class Camera:
    """An orbit camera: looks at `target` from `distance` metres, turned `yaw` around +Y (0 = from +Z, behind the
    play area's forward) and raised `pitch` (radians, positive = from above)."""
    yaw: float = 0.55
    pitch: float = 0.6
    distance: float = 8.0
    target: tuple[float, float, float] = (0.0, 1.0, 0.0)
    fov: float = math.radians(50)
    near: float = 0.05
    width: float = 800.0
    height: float = 500.0

    MIN_PITCH = math.radians(-10)
    MAX_PITCH = math.radians(89)

    @property
    def eye(self) -> tuple[float, float, float]:
        cp = math.cos(self.pitch)
        t = self.target
        return (t[0] + self.distance * cp * math.sin(self.yaw), t[1] + self.distance * math.sin(self.pitch),
                t[2] + self.distance * cp * math.cos(self.yaw))

    def basis(self):
        eye = self.eye
        f = _norm(_sub(self.target, eye))
        r = _norm(_cross(f, (0.0, 1.0, 0.0)))
        if r == (0.0, 0.0, 0.0):
            r = (math.cos(self.yaw), 0.0, -math.sin(self.yaw))
        u = _cross(r, f)
        return eye, r, u, f

    @property
    def focal(self) -> float:
        return 0.5 * max(1.0, self.height) / math.tan(self.fov / 2)

    def orbit(self, dx: float, dy: float) -> None:
        """Pixels dragged: right turns the room to the right, down raises the view."""
        self.yaw -= dx * 0.008
        self.pitch = min(self.MAX_PITCH, max(self.MIN_PITCH, self.pitch + dy * 0.008))

    def zoom(self, factor: float) -> None:
        self.distance = min(60.0, max(0.3, self.distance * factor))

    def pan(self, dx: float, dy: float) -> None:
        """Pixels dragged: the room follows the pointer."""
        _, r, u, _ = self.basis()
        k = self.distance / self.focal
        self.target = tuple(self.target[i] - r[i] * dx * k + u[i] * dy * k for i in range(3))

    def fit(self, scene: Scene) -> None:
        lo, hi = scene.lo, scene.hi
        self.target = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2 * 0.8, (lo[2] + hi[2]) / 2)
        radius = 0.5 * math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
        aspect = max(0.2, min(1.0, self.width / max(1.0, self.height)))  # a narrow canvas needs more room
        self.distance = max(1.0, radius / math.sin(self.fov / 2) / aspect * 0.88)
        self.yaw, self.pitch = 0.55, 0.6

    # projection
    def frame(self) -> Projector:
        return Projector(self)

    def project(self, points) -> list[tuple[float, float] | None]:
        """Screen positions (None = behind the camera)."""
        pr = self.frame()
        return [pr.point(p) for p in points]


class Projector:
    """One frame's camera, unpacked for speed."""

    def __init__(self, cam: Camera):
        self.eye, self.r, self.u, self.f = cam.basis()
        self.focal, self.near = cam.focal, cam.near
        self.cx, self.cy = cam.width / 2, cam.height / 2

    def cam(self, p):
        d = (p[0] - self.eye[0], p[1] - self.eye[1], p[2] - self.eye[2])
        r, u, f = self.r, self.u, self.f
        return (d[0] * r[0] + d[1] * r[1] + d[2] * r[2], d[0] * u[0] + d[1] * u[1] + d[2] * u[2],
                d[0] * f[0] + d[1] * f[1] + d[2] * f[2])

    def screen(self, c):
        k = self.focal / c[2]
        return self.cx + c[0] * k, self.cy - c[1] * k

    def point(self, p):
        c = self.cam(p)
        return self.screen(c) if c[2] >= self.near else None

    def facing(self, normal, point) -> bool:
        e = self.eye
        return normal[0] * (e[0] - point[0]) + normal[1] * (e[1] - point[1]) + normal[2] * (e[2] - point[2]) > 0

    def segment(self, a, b):
        """A clipped 3D segment on screen, or None."""
        ca, cb = self.cam(a), self.cam(b)
        n = self.near
        if ca[2] < n and cb[2] < n:
            return None
        if ca[2] < n or cb[2] < n:
            t = (n - ca[2]) / (cb[2] - ca[2])
            mid = tuple(ca[i] + (cb[i] - ca[i]) * t for i in range(3))
            ca, cb = (mid, cb) if ca[2] < n else (ca, mid)
        return self.screen(ca), self.screen(cb)

    def polygon(self, points):
        """A clipped 3D polygon on screen (Sutherland-Hodgman against the near plane), or None."""
        cs = [self.cam(p) for p in points]
        n = self.near
        if all(c[2] >= n for c in cs):
            return [self.screen(c) for c in cs]
        out = []
        for i, a in enumerate(cs):
            b = cs[(i + 1) % len(cs)]
            if a[2] >= n:
                out.append(a)
            if (a[2] >= n) != (b[2] >= n):
                t = (n - a[2]) / (b[2] - a[2])
                out.append(tuple(a[k] + (b[k] - a[k]) * t for k in range(3)))
        return [self.screen(c) for c in out] if len(out) >= 3 else None

    def depth(self, points) -> float:
        return sum(self.cam(p)[2] for p in points) / len(points)


@dataclass
class Frame2D:
    """What one redraw puts on screen (screen pixels): polygons and line segments per layer."""
    floor: list[tuple[float, float]] | None = None
    walls: list[list[tuple[float, float]]] = field(default_factory=list)
    hidden_walls: list[list[tuple[float, float]]] = field(default_factory=list)
    wall_lines: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)
    boxes: list[list[tuple[float, float]]] = field(default_factory=list)
    box_lines: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)
    mesh: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)
    grid: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)
    marker: list[tuple[float, float]] | None = None  # where the play area's centre is, pointing forward (-Z)
    planes: list[list[tuple[float, float]]] = field(default_factory=list)  # doors / windows
    selected: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)

    @property
    def segments(self) -> int:
        return len(self.wall_lines) + len(self.box_lines) + len(self.mesh) + len(self.grid) + len(self.selected)


def render(scene: Scene, cam: Camera, lod: str = "rest") -> Frame2D:
    """Project the scene: far sides only (faces turned away from the camera are left out), back to front."""
    pr = cam.frame()
    out = Frame2D()
    out.floor = pr.polygon(scene.floor) if scene.floor else None
    for a, b in scene.grid:
        s = pr.segment(a, b)
        if s:
            out.grid.append(s)
    walls = []
    for face in scene.faces:
        if face.kind == "box" or not pr.facing(face.normal, face.points[0]):
            continue
        poly = pr.polygon(face.points)
        if poly:
            walls.append((pr.depth(face.points), face, poly))
    walls.sort(key=lambda w: -w[0])
    for _, face, poly in walls:
        (out.hidden_walls if face.kind == "hidden_wall" else out.walls).append(poly)
        if face.kind == "wall":
            p = face.points
            for a, b in ((p[0], p[3]), (p[1], p[2]), (p[3], p[2])):  # the sides and the top
                s = pr.segment(a, b)
                if s:
                    out.wall_lines.append(s)
    boxes = []
    for face in scene.faces:
        if face.kind == "box" and pr.facing(face.normal, face.points[0]):
            poly = pr.polygon(face.points)
            if poly:
                boxes.append((pr.depth(face.points), poly))
    boxes.sort(key=lambda b: -b[0])
    out.boxes = [poly for _, poly in boxes]
    for a, b in scene.box_edges:
        s = pr.segment(a, b)
        if s:
            out.box_lines.append(s)
    m = scene.lods.get(lod) or scene.lods.get("rest")
    if m is not None:
        e = pr.eye
        face_on = [n[0] * (e[0] - c[0]) + n[1] * (e[1] - c[1]) + n[2] * (e[2] - c[2]) > 0
                   for n, c in zip(m.normals, m.centers, strict=True)]
        cams = [pr.cam(p) for p in m.vertices]
        near = pr.near
        pts = [pr.screen(c) if c[2] >= near else None for c in cams]
        for u, v, owners in m.edges:
            pu, pv = pts[u], pts[v]
            if pu is None or pv is None:
                continue
            for t in owners:
                if face_on[t]:
                    out.mesh.append((pu, pv))
                    break
    for corners in scene.planes:
        poly = pr.polygon(corners)
        if poly:
            out.planes.append(poly)
    for a, b in scene.selected:
        s = pr.segment(a, b)
        if s:
            out.selected.append(s)
    y = scene.floor[0][1] if scene.floor else 0.0
    tri = [(0.0, y, -0.35), (0.2, y, 0.15), (-0.2, y, 0.15)]
    out.marker = pr.polygon(tri)
    return out
