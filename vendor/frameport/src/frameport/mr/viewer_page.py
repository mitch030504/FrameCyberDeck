# ruff: noqa: E501  (the embedded page's JavaScript)
"""The Room tab's full 3D view page (served by room_viewer.py): hand-written WebGL 1, no libraries and nothing from
the network. A Python string rather than an .html file: the PyInstaller fallback bundle only carries data files it
is told about (like install/live_player.py).

Data: `room.json` (walls, furniture boxes with their corners, outline) and `mesh.bin` (the room file's FPRM v1 mesh,
read with a DataView), both relative to the page (the page's path carries the session token). `version` changes when
the PC has a newer room: the page reloads its data by itself."""

VIEWER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FramePort Room</title>
<style>
  :root { --bg: #0b0d10; --panel: rgba(20, 23, 28, .88); --text: #e8eaed; --muted: #9aa0a6; --blue: #3aa8ff;
          --orange: #ff8a3d; --border: #2b3036; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
               font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; overflow: hidden; }
  canvas { position: fixed; inset: 0; width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
  canvas.drag { cursor: grabbing; }
  .panel { position: fixed; background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
           padding: 10px 12px; }
  #info { left: 12px; top: 12px; max-width: min(360px, calc(100% - 48px)); }
  #info h1 { font-size: 15px; margin: 0 0 4px; font-weight: 600; }
  #info .muted, .muted { color: var(--muted); }
  #legend { right: 12px; top: 12px; max-height: calc(100% - 120px); overflow: auto; min-width: 150px; }
  #legend div { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
  #legend i { width: 10px; height: 10px; border-radius: 2px; flex: none; }
  #bar { left: 12px; bottom: 12px; right: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  #bar .help { flex: 1; min-width: 200px; color: var(--muted); }
  label.t { display: inline-flex; gap: 6px; align-items: center; padding: 3px 8px; border: 1px solid var(--border);
            border-radius: 6px; cursor: pointer; user-select: none; }
  label.t input { accent-color: var(--orange); margin: 0; }
  button { background: transparent; color: var(--text); border: 1px solid var(--border); border-radius: 6px;
           padding: 4px 10px; font: inherit; cursor: pointer; }
  button:hover, label.t:hover { border-color: var(--blue); }
  #labels { position: fixed; inset: 0; pointer-events: none; overflow: hidden; }
  #labels span { position: absolute; transform: translate(-50%, -100%); font-size: 11px; font-weight: 600;
                 padding: 1px 6px; border-radius: 4px; background: rgba(0, 0, 0, .55); white-space: nowrap; }
  #msg { left: 50%; top: 50%; transform: translate(-50%, -50%); display: none; text-align: center; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="labels"></div>
<div id="info" class="panel"><h1>Room</h1><div id="sum" class="muted">Loading…</div></div>
<div id="legend" class="panel"></div>
<div id="msg" class="panel"></div>
<div id="bar" class="panel">
  <label class="t"><input type="checkbox" id="tMesh" checked>Scanned surfaces</label>
  <label class="t"><input type="checkbox" id="tWalls" checked>Walls</label>
  <label class="t"><input type="checkbox" id="tBoxes" checked>Furniture</label>
  <label class="t"><input type="checkbox" id="tLabels" checked>Names</label>
  <label class="t" title="Hide the sides that face away from you, so you can look in from outside"><input type="checkbox" id="tCull" checked>See inside</label>
  <button id="fit">Reset view</button>
  <span class="help">Drag to turn · Scroll or pinch to zoom · Right-drag or Shift-drag to move · Double-click to reset</span>
</div>
<script>
"use strict";
const LABEL_COLORS = { TABLE: "#ff8a3d", COUCH: "#c084fc", BED: "#f472b6", STORAGE: "#facc15", SCREEN: "#22d3ee",
  LAMP: "#fde68a", PLANT: "#4ade80", DOOR_FRAME: "#a3e635", WINDOW_FRAME: "#93c5fd", WALL_ART: "#fb7185",
  OTHER: "#cbd5e1" };
const NAMES = { TABLE: "Table", COUCH: "Couch", BED: "Bed", STORAGE: "Storage", SCREEN: "Screen", LAMP: "Lamp",
  PLANT: "Plant", DOOR_FRAME: "Door", WINDOW_FRAME: "Window", WALL_ART: "Wall art", OTHER: "Other" };
const nice = l => NAMES[l] || l.charAt(0) + l.slice(1).toLowerCase().replace(/_/g, " ");
const colorOf = l => LABEL_COLORS[l] || "#cbd5e1";
const hex = h => [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255];
const $ = id => document.getElementById(id);

const canvas = $("c");
const gl = canvas.getContext("webgl", { antialias: true, alpha: false });

// ---------------------------------------------------------------- matrices (column-major, like GL)
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function norm(a) { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function lookAt(eye, at, up) {
  const z = norm(sub(eye, at)), x = norm(cross(up, z)), y = cross(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}
function mul(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s;
  }
  return o;
}

// ---------------------------------------------------------------- shaders
function program(vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}
const meshProg = program(
  "attribute vec3 p; attribute vec3 n; uniform mat4 mvp; varying vec3 vn;" +
  "void main() { vn = n; gl_Position = mvp * vec4(p, 1.0); }",
  "precision mediump float; varying vec3 vn; uniform vec3 eyeDir;" +
  "void main() { vec3 n = normalize(vn); if (!gl_FrontFacing) n = -n;" +
  " float up = clamp(n.y, 0.0, 1.0); vec3 base = mix(vec3(0.23, 0.56, 0.86), vec3(0.62, 0.66, 0.72), up);" +
  " float l = 0.38 + 0.5 * max(dot(n, normalize(vec3(0.4, 0.9, 0.3))), 0.0) + 0.22 * max(dot(n, -eyeDir), 0.0);" +
  " gl_FragColor = vec4(base * l, 1.0); }");
const flatProg = program(
  "attribute vec3 p; uniform mat4 mvp; void main() { gl_Position = mvp * vec4(p, 1.0); }",
  "precision mediump float; uniform vec4 color; void main() { gl_FragColor = color; }");

function buffer(data) { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); return b; }

// ---------------------------------------------------------------- data
let room = null, meshBuf = null, meshCount = 0, overlays = [], bounds = null, version = null;

function parseFPRM(ab) {
  const dv = new DataView(ab);
  if (ab.byteLength < 16) throw new Error("mesh file is too short");
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  const ver = dv.getUint32(4, true), nv = dv.getUint32(8, true), ni = dv.getUint32(12, true);
  if (magic !== "FPRM" || ver !== 1) throw new Error("not an FPRM v1 mesh");
  if (ni % 3 || ab.byteLength !== 16 + nv * 12 + ni * 4) throw new Error("mesh size doesn't match its header");
  const v = new Float32Array(nv * 3);
  for (let i = 0; i < nv * 3; i++) v[i] = dv.getFloat32(16 + i * 4, true);
  const pos = new Float32Array(ni * 3), nrm = new Float32Array(ni * 3);
  const base = 16 + nv * 12;
  for (let t = 0; t < ni; t += 3) {
    const a = dv.getUint32(base + t * 4, true), b = dv.getUint32(base + t * 4 + 4, true), c = dv.getUint32(base + t * 4 + 8, true);
    if (a >= nv || b >= nv || c >= nv) throw new Error("mesh index out of range");
    const A = [v[a * 3], v[a * 3 + 1], v[a * 3 + 2]], B = [v[b * 3], v[b * 3 + 1], v[b * 3 + 2]], C = [v[c * 3], v[c * 3 + 1], v[c * 3 + 2]];
    const n = norm(cross(sub(B, A), sub(C, A)));
    [A, B, C].forEach((P, k) => { pos.set(P, (t + k) * 3); nrm.set(n, (t + k) * 3); });
  }
  return { pos, nrm, count: ni };
}

function addOverlay(mode, data, color, layer) {
  overlays.push({ mode, buf: buffer(new Float32Array(data)), count: data.length / 3, color, layer });
}

function buildOverlays() {
  overlays = [];
  const f = room.floor, outline = room.outline;
  const loop = [];
  outline.forEach((p, i) => { const q = outline[(i + 1) % outline.length]; loop.push(p[0], f, p[1], q[0], f, q[1]); });
  addOverlay(gl.LINES, loop, [1, 0.54, 0.24, 0.9], "walls");
  const fill = [];  // floor: a fan from the first point (outlines are simple polygons; good enough for a tint)
  for (let i = 1; i + 1 < outline.length; i++) fill.push(outline[0][0], f, outline[0][1], outline[i][0], f, outline[i][1], outline[i + 1][0], f, outline[i + 1][1]);
  addOverlay(gl.TRIANGLES, fill, [0.23, 0.66, 1, 0.07], "walls");
  for (const w of room.walls) {
    const quad = [w.x0, w.y0, w.z0, w.x1, w.y0, w.z1, w.x1, w.y1, w.z1, w.x0, w.y0, w.z0, w.x1, w.y1, w.z1, w.x0, w.y1, w.z0];
    const hidden = w.label === "INVISIBLE_WALL_FACE";
    addOverlay(gl.TRIANGLES, quad, hidden ? [0.23, 0.66, 1, 0.04] : [0.23, 0.66, 1, 0.12], "walls");
    if (!hidden) addOverlay(gl.LINES, [w.x0, w.y0, w.z0, w.x0, w.y1, w.z0, w.x0, w.y1, w.z0, w.x1, w.y1, w.z1], [0.23, 0.66, 1, 0.7], "walls");
  }
  for (const v of room.volumes) {
    const c = v.corners, top = v.top_y, bot = v.top_y - v.h, col = hex(colorOf(v.label));
    const lines = [];
    for (let i = 0; i < 4; i++) {
      const a = c[i], b = c[(i + 1) % 4];
      lines.push(a[0], bot, a[1], b[0], bot, b[1], a[0], top, a[1], b[0], top, b[1], a[0], bot, a[1], a[0], top, a[1]);
    }
    addOverlay(gl.LINES, lines, [...col, 1], "boxes");
    const tris = [];
    const P = (i, y) => [c[i][0], y, c[i][1]];
    const face = (a, b, cc, d) => tris.push(...a, ...b, ...cc, ...a, ...cc, ...d);
    face(P(0, top), P(1, top), P(2, top), P(3, top));
    for (let i = 0; i < 4; i++) { const j = (i + 1) % 4; face(P(i, bot), P(j, bot), P(j, top), P(i, top)); }
    addOverlay(gl.TRIANGLES, tris, [...col, 0.18], "boxes");
  }
}

function computeBounds(pos) {
  const lo = [Infinity, room.floor, Infinity], hi = [-Infinity, room.ceiling, -Infinity];
  for (const [x, z] of room.outline) { lo[0] = Math.min(lo[0], x); hi[0] = Math.max(hi[0], x); lo[2] = Math.min(lo[2], z); hi[2] = Math.max(hi[2], z); }
  if (pos) {  // the mesh, without its far-off stray points: 1st..99th percentile per axis on a sample
    const step = Math.max(1, Math.floor(pos.length / 3 / 4000));
    for (let k = 0; k < 3; k++) {
      const s = []; for (let i = k; i < pos.length; i += 3 * step) s.push(pos[i]);
      s.sort((a, b) => a - b);
      if (s.length) { lo[k] = Math.min(lo[k], s[Math.floor(s.length * 0.01)]); hi[k] = Math.max(hi[k], s[Math.floor(s.length * 0.99)]); }
    }
  }
  return { lo, hi };
}

function legend() {
  const counts = {};
  for (const v of room.volumes) counts[v.label] = (counts[v.label] || 0) + 1;
  const el = $("legend"); el.textContent = "";
  const head = document.createElement("div"); head.className = "muted"; head.textContent = "Furniture"; el.appendChild(head);
  const keys = Object.keys(counts).sort();
  if (!keys.length) { const d = document.createElement("div"); d.className = "muted"; d.textContent = "None found"; el.appendChild(d); }
  for (const k of keys) {
    const d = document.createElement("div"), i = document.createElement("i"), s = document.createElement("span");
    i.style.background = colorOf(k); s.textContent = nice(k) + (counts[k] > 1 ? " × " + counts[k] : "");
    d.append(i, s); el.appendChild(d);
  }
  const labels = $("labels"); labels.textContent = "";
  room.volumes.forEach(v => { const s = document.createElement("span"); s.textContent = nice(v.label); s.style.color = colorOf(v.label); v.el = s; labels.appendChild(s); });
}

function summary() {
  const s = room.summary || {};
  const src = { scan: "Scanned room", boundary: "Play area walls", box: "Simple room", manual: "Room" }[room.source] || "Room";
  const parts = [s.walls + " walls", (s.floor_area || 0).toFixed(1) + " m²", (s.ceiling_height || 0).toFixed(1) + " m high", room.volumes.length + " pieces of furniture"];
  if (meshCount) parts.push(Math.round(meshCount / 3).toLocaleString() + " mesh triangles");
  $("sum").textContent = src + " · " + parts.join(" · ");
}

async function load() {
  const r = await fetch("room.json", { cache: "no-store" });
  if (!r.ok) throw new Error(r.status === 404 ? "No room yet" : "Couldn't load the room (" + r.status + ")");
  const data = await r.json();
  room = data; version = data.version;
  let pos = null;
  if (data.has_mesh) {
    const m = await fetch("mesh.bin", { cache: "no-store" });
    if (m.ok) {
      const parsed = parseFPRM(await m.arrayBuffer());
      pos = parsed.pos;
      if (meshBuf) { gl.deleteBuffer(meshBuf.p); gl.deleteBuffer(meshBuf.n); }
      meshBuf = { p: buffer(parsed.pos), n: buffer(parsed.nrm) }; meshCount = parsed.count;
    }
  } else { meshBuf = null; meshCount = 0; }
  for (const o of overlays) gl.deleteBuffer(o.buf);
  buildOverlays(); legend(); summary();
  bounds = computeBounds(pos); fit(); $("msg").style.display = "none"; draw();
}

// ---------------------------------------------------------------- camera
const cam = { yaw: 0.55, pitch: 0.6, dist: 8, target: [0, 1, 0] };
function fit() {
  if (!bounds) return;
  const { lo, hi } = bounds;
  cam.target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2 * 0.8, (lo[2] + hi[2]) / 2];
  const r = 0.5 * Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
  const aspect = Math.min(1, canvas.clientWidth / Math.max(1, canvas.clientHeight));
  cam.dist = Math.max(1, r / Math.sin(0.4363) / Math.max(0.4, aspect) * 0.9); cam.yaw = 0.55; cam.pitch = 0.6;
}
function eye() {
  const cp = Math.cos(cam.pitch);
  return [cam.target[0] + cam.dist * cp * Math.sin(cam.yaw), cam.target[1] + cam.dist * Math.sin(cam.pitch), cam.target[2] + cam.dist * cp * Math.cos(cam.yaw)];
}

let queued = false;
function draw() { if (!queued) { queued = true; requestAnimationFrame(render); } }

function render() {
  queued = false;
  const dpr = window.devicePixelRatio || 1, w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  gl.viewport(0, 0, w, h);
  gl.clearColor(0.043, 0.051, 0.063, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  if (!room) return;
  const e = eye(), proj = perspective(0.8727, w / Math.max(1, h), 0.03, 200), view = lookAt(e, cam.target, [0, 1, 0]);
  const mvp = mul(proj, view);
  const cull = $("tCull").checked;
  gl.enable(gl.DEPTH_TEST); gl.depthMask(true); gl.disable(gl.BLEND);
  if (cull) { gl.enable(gl.CULL_FACE); gl.cullFace(gl.BACK); gl.frontFace(gl.CCW); } else gl.disable(gl.CULL_FACE);
  if (meshBuf && $("tMesh").checked) {
    gl.useProgram(meshProg);
    gl.uniformMatrix4fv(gl.getUniformLocation(meshProg, "mvp"), false, mvp);
    gl.uniform3fv(gl.getUniformLocation(meshProg, "eyeDir"), norm(sub(cam.target, e)));
    const lp = gl.getAttribLocation(meshProg, "p"), ln = gl.getAttribLocation(meshProg, "n");
    gl.bindBuffer(gl.ARRAY_BUFFER, meshBuf.p); gl.enableVertexAttribArray(lp); gl.vertexAttribPointer(lp, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, meshBuf.n); gl.enableVertexAttribArray(ln); gl.vertexAttribPointer(ln, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, meshCount);
    gl.disableVertexAttribArray(ln);
  }
  gl.useProgram(flatProg);
  gl.uniformMatrix4fv(gl.getUniformLocation(flatProg, "mvp"), false, mvp);
  const lp = gl.getAttribLocation(flatProg, "p"), lc = gl.getUniformLocation(flatProg, "color");
  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.disable(gl.CULL_FACE);
  const shown = o => (o.layer === "walls" && $("tWalls").checked) || (o.layer === "boxes" && $("tBoxes").checked);
  for (const pass of [gl.LINES, gl.TRIANGLES]) {  // lines first with depth, translucent fills after without writing depth
    gl.depthMask(pass === gl.LINES);
    for (const o of overlays) {
      if (o.mode !== pass || !shown(o)) continue;
      gl.bindBuffer(gl.ARRAY_BUFFER, o.buf); gl.enableVertexAttribArray(lp); gl.vertexAttribPointer(lp, 3, gl.FLOAT, false, 0, 0);
      gl.uniform4fv(lc, o.color); gl.drawArrays(o.mode, 0, o.count);
    }
  }
  gl.depthMask(true);
  const showLabels = $("tLabels").checked && $("tBoxes").checked;
  for (const v of room.volumes) {  // names above each piece of furniture
    const p = [v.cx, v.top_y + 0.05, v.cz, 1];
    const c = [0, 1, 2, 3].map(r => mvp[r] * p[0] + mvp[4 + r] * p[1] + mvp[8 + r] * p[2] + mvp[12 + r]);
    const vis = showLabels && c[3] > 0.05;
    v.el.style.display = vis ? "" : "none";
    if (vis) { v.el.style.left = ((c[0] / c[3] + 1) / 2 * canvas.clientWidth) + "px"; v.el.style.top = ((1 - c[1] / c[3]) / 2 * canvas.clientHeight) + "px"; }
  }
}

// ---------------------------------------------------------------- input
const pointers = new Map();
let mode = null, last = null, pinch = 0;
canvas.addEventListener("contextmenu", ev => ev.preventDefault());
canvas.addEventListener("pointerdown", ev => {
  canvas.setPointerCapture(ev.pointerId); pointers.set(ev.pointerId, [ev.clientX, ev.clientY]);
  mode = (ev.button === 2 || ev.shiftKey || pointers.size > 1) ? "pan" : "orbit"; last = [ev.clientX, ev.clientY];
  if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); }
  canvas.classList.add("drag");
});
canvas.addEventListener("pointermove", ev => {
  if (!pointers.has(ev.pointerId)) return;
  pointers.set(ev.pointerId, [ev.clientX, ev.clientY]);
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
    if (pinch) cam.dist = Math.min(60, Math.max(0.3, cam.dist * pinch / d)); pinch = d;
  }
  const dx = ev.clientX - last[0], dy = ev.clientY - last[1]; last = [ev.clientX, ev.clientY];
  if (mode === "orbit") { cam.yaw -= dx * 0.008; cam.pitch = Math.min(1.553, Math.max(-0.17, cam.pitch + dy * 0.008)); }
  else {
    const e = eye(), f = norm(sub(cam.target, e)), r = norm(cross(f, [0, 1, 0])), u = cross(r, f);
    const k = cam.dist * 2 * Math.tan(0.4363) / Math.max(1, canvas.clientHeight) / (pointers.size > 1 ? 2 : 1);
    cam.target = cam.target.map((t, i) => t - r[i] * dx * k + u[i] * dy * k);
  }
  draw();
});
const up = ev => { pointers.delete(ev.pointerId); if (!pointers.size) { mode = null; canvas.classList.remove("drag"); } pinch = 0; };
canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
canvas.addEventListener("wheel", ev => { ev.preventDefault(); cam.dist = Math.min(60, Math.max(0.3, cam.dist * Math.pow(1.0015, ev.deltaY))); draw(); }, { passive: false });
canvas.addEventListener("dblclick", () => { fit(); draw(); });
$("fit").onclick = () => { fit(); draw(); };
for (const id of ["tMesh", "tWalls", "tBoxes", "tLabels", "tCull"]) $(id).onchange = draw;
window.addEventListener("resize", draw);

function fail(text) { const m = $("msg"); m.textContent = text; m.style.display = "block"; $("sum").textContent = text; }
if (!gl) fail("This browser can't show 3D (WebGL is off).");
else {
  load().catch(err => fail(err.message));
  setInterval(async () => {  // a new scan on the PC: load it
    try { const r = await fetch("version", { cache: "no-store" }); if (r.ok) { const v = (await r.text()).trim(); if (version !== null && v !== String(version)) await load(); } } catch (e) { /* FramePort closed */ }
  }, 3000);
}
</script>
</body>
</html>
"""
