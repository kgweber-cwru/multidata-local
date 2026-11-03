// Encounter Explorer. Plain JS, no libraries. Data comes from
// demo/build_case.py (data/demo/cases/<case>/) and demo/build_overview.py
// (data/demo/overview.json), served by demo/serve.py.
"use strict";

const ROLE_COLORS = {
  LEARNER: "#4ea1ff", PATIENT: "#ff9f43", PRECEPTOR: "#2ecc9a",
  ANNOUNCEMENT: "#8a8f98", OUTSIDE_ROOM: "#b48ead", person: "#d7dbe0",
};
const RANK_COLORS = ["#4ea1ff", "#ff9f43", "#2ecc9a", "#c792ea", "#f7768e", "#e0af68", "#7dcfff"];
const LINKS = [[15, 13], [13, 11], [11, 19], [16, 14], [14, 12], [12, 19], [17, 18], [18, 19],
  [18, 5], [5, 7], [7, 9], [18, 6], [6, 8], [8, 10], [1, 2], [0, 1], [0, 2],
  [1, 3], [2, 4], [15, 20], [15, 22], [15, 24], [16, 21], [16, 23], [16, 25]];
const PAUSE_MIN = 2.0;
const SHORT = { ANNOUNCEMENT: "announce", OUTSIDE_ROOM: "outside" };             // gaps in conversation at least this long are marked

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
const fmt = (t) => { t = Math.max(0, t); const m = Math.floor(t / 60); return `${m}:${(t % 60).toFixed(1).padStart(4, "0")}`; };
const fmtShort = (t) => { t = Math.max(0, t); return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`; };

let S = null;          // the loaded case
let OVERVIEW = null;
let BUILT = {};
const DEBUG = new URLSearchParams(location.hash.slice(1)).get("debug") === "1";

function roleColor(role) {
  if (ROLE_COLORS[role]) return ROLE_COLORS[role];
  const i = S ? S.meta.roles.indexOf(role) : 0;
  return RANK_COLORS[(i < 0 ? 0 : i) % RANK_COLORS.length];
}

// ---------------------------------------------------------------- loading

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function init() {
  try { BUILT = await getJSON("data/cases/index.json"); } catch { BUILT = {}; }
  try { OVERVIEW = await getJSON("data/overview.json"); } catch { OVERVIEW = null; }
  const sel = $("#caseSelect");
  for (const [id, b] of Object.entries(BUILT)) {
    const o = el("option", null, `${id} · room ${b.room}`);
    o.value = id;
    sel.append(o);
  }
  sel.onchange = () => loadCase(sel.value);
  document.querySelectorAll(".tab").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
  wireControls();
  if (OVERVIEW) buildAll();
  const hash = new URLSearchParams(location.hash.slice(1));
  const first = hash.get("case") || (BUILT["261456"] ? "261456" : Object.keys(BUILT)[0]);
  if (first) { sel.value = first; await loadCase(first, parseFloat(hash.get("t") || "0")); }
  if (!first || hash.get("tab") === "all") showTab("all");
  requestAnimationFrame(tick);
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("#one").classList.toggle("hidden", name !== "one");
  $("#all").classList.toggle("hidden", name !== "all");
  $(".case-pick").classList.toggle("hidden", name !== "one");
  if (name === "all") drawAllStrips();
  else if (S) { pauseAll(); resizeCanvases(); }
}

async function loadCase(id, t0 = 0) {
  if (S) teardown();
  const base = `data/cases/${id}/`;
  const [meta, lanes] = await Promise.all([getJSON(base + "meta.json"), getJSON(base + "lanes.json")]);
  const skel = {};
  await Promise.all(meta.cameras.map(async (c) => {
    const buf = await (await fetch(base + c.skeletons)).arrayBuffer();
    skel[c.camera] = new Int16Array(buf);
  }));
  let three = null;
  try { const r = await fetch(base + "threed.json"); if (r.ok) three = await r.json(); } catch { three = null; }
  S = { id, base, meta, lanes, skel, three, mainIdx: 0, pix: $("#pixToggle").checked, vids: [], lastNow: -1 };
  S.utts = lanes.utterances.map(([a, b, role, text], i) => ({ i, a, b, role, text }));
  S.pauses = findPauses(S.utts);
  S.scales = scales(lanes);
  const src = meta.transcript_source || "";
  const badge = $("#sourceBadge");
  badge.textContent = `Transcript: ${src}`;
  badge.className = "badge " + (src.startsWith("machine") ? "machine" : "human");
  buildVideos();
  buildTranscript();
  buildBookmarks();
  buildLegend();
  const notes = meta.cameras.filter((c) => c.offset_note !== "audio sync")
    .map((c) => `Camera ${c.camera} sync: ${c.offset_note}.`);
  $("#syncNote").textContent = notes.join(" ");
  $("#fineprint").textContent = fineprint();
  $("#headStats").textContent = headStats();
  resizeCanvases();
  seek(t0);
  history.replaceState(null, "", `#case=${id}` + (DEBUG ? "&debug=1" : ""));
}

function teardown() {
  for (const v of S.vids) { v.video.pause(); v.video.removeAttribute("src"); v.video.load(); }
  $("#videos").innerHTML = "";
}

// ---------------------------------------------------------------- derived data

function findPauses(utts) {
  const talk = utts.filter((u) => u.role !== "ANNOUNCEMENT").sort((x, y) => x.a - y.a);
  const out = [];
  let end = -1;
  for (const u of talk) {
    if (end >= 0 && u.a - end >= PAUSE_MIN) out.push([end, u.a]);
    end = Math.max(end, u.b);
  }
  return out;
}

function pct(arr, p) {
  const v = arr.filter((x) => x != null).sort((a, b) => a - b);
  return v.length ? v[Math.min(v.length - 1, Math.floor(p * v.length))] : 1;
}

function scales(lanes) {
  const s = {};
  if (lanes.prosody) {
    s.loud = [pct(lanes.prosody.loudness_db, 0.05), pct(lanes.prosody.loudness_db, 0.995)];
  }
  s.motion = {};
  for (const [cam, roles] of Object.entries(lanes.motion)) {
    const all = [].concat(...Object.values(roles));
    s.motion[cam] = Math.max(pct(all, 0.985), 1e-3);
  }
  return s;
}

function fineprint() {
  const m = S.meta;
  return `Case ${m.case}, room ${m.room}. Talk and speech rate from the transcript; pitch and loudness from the ` +
    `audio (Praat), pitch shown only where one person speaks alone. Movement: how fast each person's body keypoints move ` +
    `(2D pose), on the large camera, blank while the camera moves or the person is out of view; ` +
    (Object.keys(S.lanes.motion).some((c) => Object.keys(S.lanes.motion[c]).some((r) => r !== "person"))
      ? "people are identified by hand-checked tracking. " : "people aren't identified in this case. ") + `Pauses: gaps of ${PAUSE_MIN}+ s between anyone's speech. Head direction is a gaze ` +
    `proxy from the head keypoints (face direction and head tilt), not eye tracking: coloured = toward the other ` +
    `person, grey = looking down, red = elsewhere. ` +
    ((S.lanes.head || {})["3D"] ? `Here it comes from the 3D reconstruction, so it covers only the 3D stretch; ` +
      `"toward" means within ${35}° of the other person's head, so looking at their hands or legs during an exam counts as down or elsewhere.`
      : `It comes from one camera's 2D view.`) +
    (S.three ? ` 3D (purple bar on the timeline): an uncalibrated reconstruction from the two cameras; the cameras ` +
      `calibrated themselves from people's keypoints and the room's lines, size from typical body proportions. ` +
      (S.three.frames.some((f) => f.some((p) => p.who))
        ? `People are named from the hand-checked tracking on one camera.`
        : `"patient?"/"learner?" is a guess (who stays put), not identification.`) : "");
}

function headStats() {
  // share of time each person's head pointed at the other, by who was speaking
  const H = Object.values(S.lanes.head || {})[0];
  if (!H) return "";
  const hz = S.meta.motion_hz, parts = [];
  for (const [me, sq] of Object.entries(H)) {
    const other = me === "LEARNER" ? "PATIENT" : "LEARNER";
    for (const [label, who] of [["while the other spoke", other], ["while speaking", me]]) {
      let n = 0, toward = 0, down = 0;
      for (const u of S.utts) {
        if (u.role !== who) continue;
        for (let i = Math.floor(u.a * hz); i < Math.min(sq.length, u.b * hz); i++) {
          if (sq[i] === "0") continue;
          n++; if (sq[i] === "1") toward++; if (sq[i] === "2") down++;
        }
      }
      if (n > 20) parts.push(`${me.toLowerCase()} ${label}: faced the ${other.toLowerCase()} ${Math.round(100 * toward / n)}%, looked down ${Math.round(100 * down / n)}%`);
    }
  }
  return parts.length ? "Head direction · " + parts.join(" · ") : "";
}

// ---------------------------------------------------------------- video

function videoSrc(c) { return S.base + (S.pix ? c.video_pix : c.video); }

function buildVideos() {
  $("#videos").innerHTML = "";
  S.vids = S.meta.cameras.map((c, i) => {
    const wrap = el("div", "cam");
    const video = el("video");
    video.preload = "auto";
    video.playsInline = true;
    video.muted = !c.audio;
    video.src = videoSrc(c);
    const canvas = el("canvas");
    const label = el("div", "cam-label");
    wrap.append(video, canvas, label);
    wrap.onclick = () => { if (wrap.classList.contains("small")) { S.mainIdx = i; layoutVideos(); } };
    video.addEventListener("click", (ev) => { if (!wrap.classList.contains("small")) { ev.stopPropagation(); togglePlay(); } });
    return { cam: c, wrap, video, canvas, label };
  });
  S.view3d = S.three ? build3d() : null;
  layoutVideos();
}

function layoutVideos() {
  // Place cameras with grid positions only: moving a <video> in the DOM can
  // stall its loading.
  const box = $("#videos"), n = S.vids.length;
  if (!box.children.length) for (const v of S.vids) box.append(v.wrap);
  box.style.gridTemplateColumns = n > 1 ? "" : "1fr";
  let k = 1;
  const rows = Math.max(n - 1, 1) + (S.view3d ? 1 : 0);
  S.vids.forEach((v, i) => {
    const main = i === S.mainIdx;
    v.wrap.classList.toggle("small", !main);
    v.wrap.style.gridColumn = main ? "1" : "2";
    v.wrap.style.gridRow = main ? `1 / span ${rows}` : String(k++);
  });
  if (S.view3d) {
    if (!S.view3d.wrap.parentElement) box.append(S.view3d.wrap);
    S.view3d.wrap.style.gridColumn = "2";
    S.view3d.wrap.style.gridRow = String(k);
  }
  requestAnimationFrame(resizeCanvases);
}

function master() { return S.vids.find((v) => v.cam.audio) || S.vids[0]; }
function now() { return master().video.currentTime + master().cam.offset; }

function seek(t) {
  t = Math.max(0, Math.min(t, S.meta.duration_s - 0.05));
  for (const v of S.vids) {
    const ft = t - v.cam.offset;
    if (isFinite(ft)) v.video.currentTime = Math.max(0, ft);
  }
}

function togglePlay() {
  const m = master().video;
  if (m.paused) {
    for (const v of S.vids) { v.video.playbackRate = parseFloat($("#speed").value); v.video.play().catch(() => {}); }
  } else pauseAll();
}
function pauseAll() { if (S) for (const v of S.vids) v.video.pause(); }

function syncSlaves(t) {
  const m = master().video;
  for (const v of S.vids) {
    if (v === master()) continue;
    const want = t - v.cam.offset;
    if (want < 0) continue;
    if (Math.abs(v.video.currentTime - want) > 0.25 && !v.video.seeking) v.video.currentTime = want;
    if (m.paused && !v.video.paused) v.video.pause();
    if (!m.paused && v.video.paused) v.video.play().catch(() => {});
    v.video.playbackRate = m.playbackRate;
  }
}

function setPixelated(on) {
  if (!S) return;
  const t = now(), playing = !master().video.paused;
  S.pix = on;
  for (const v of S.vids) {
    v.video.src = videoSrc(v.cam);
    v.video.addEventListener("loadedmetadata", () => {
      v.video.currentTime = Math.max(0, t - v.cam.offset);
      if (playing) v.video.play().catch(() => {});
    }, { once: true });
  }
}

// ---------------------------------------------------------------- skeletons

function drawSkeletons(t) {
  const on = $("#skelToggle").checked;
  const sk = S.meta.skeleton;
  const g = Math.round(t * sk.fps);
  const stride = sk.people * 53;
  for (const v of S.vids) {
    const cv = v.canvas, ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    const view = viewAt(v.cam, t);
    v.label.textContent = `Camera ${v.cam.camera}${view ? " · " + view : ""}${v.cam.audio ? " · sound" : ""}` +
      (DEBUG ? ` · rs${v.video.readyState} ns${v.video.networkState} t${v.video.currentTime.toFixed(1)}${v.video.error ? " err" + v.video.error.code : ""}` : "");
    if (!on) continue;
    const data = S.skel[v.cam.camera];
    if (!data || g < 0 || (g + 1) * stride > data.length) continue;
    // the video is letterboxed inside the 16:9 box
    const ar = v.cam.width / v.cam.height, bw = cv.width, bh = cv.height;
    let w = bw, h = bw / ar;
    if (h > bh) { h = bh; w = bh * ar; }
    const ox = (bw - w) / 2, oy = (bh - h) / 2, sc = w / v.cam.width;
    const lw = Math.max(1.5, cv.width / 500);
    for (let p = 0; p < sk.people; p++) {
      const o = g * stride + p * 53;
      const code = data[o];
      if (!code) continue;
      const col = ROLE_COLORS[sk.roles[code]] || "#ddd";
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = lw; ctx.globalAlpha = 0.9;
      const X = (k) => data[o + 1 + 2 * k], Y = (k) => data[o + 2 + 2 * k];
      ctx.beginPath();
      for (const [a, b] of LINKS) {
        if (X(a) < 0 || X(b) < 0) continue;
        ctx.moveTo(ox + X(a) * sc, oy + Y(a) * sc);
        ctx.lineTo(ox + X(b) * sc, oy + Y(b) * sc);
      }
      ctx.stroke();
      for (let k = 5; k < 26; k++) {
        if (X(k) < 0 || k === 17) continue;
        ctx.beginPath(); ctx.arc(ox + X(k) * sc, oy + Y(k) * sc, lw * 1.3, 0, 6.29); ctx.fill();
      }
      const role = sk.roles[code];
      if (role !== "person" && X(17) >= 0 && !v.wrap.classList.contains("small")) {
        ctx.globalAlpha = 1;
        const fs = Math.round(Math.max(12, cv.width / 70)), txt = role.toLowerCase();
        ctx.font = `600 ${fs}px system-ui`;
        const tw = ctx.measureText(txt).width, lx = ox + X(17) * sc - tw / 2, ly = oy + Y(17) * sc - fs;
        ctx.fillStyle = "rgba(0,0,0,.6)"; ctx.fillRect(lx - 4, ly - fs, tw + 8, fs * 1.35);
        ctx.fillStyle = col; ctx.fillText(txt, lx, ly);
      }
    }
    ctx.globalAlpha = 1;
  }
}

function viewAt(cam, t) {
  const s = cam.views.find((s) => t >= s.t0 && t < s.t1);
  return s ? s.label : "moving";
}

// ---------------------------------------------------------------- 3D view

const SKEL3D = [[15, 13], [13, 11], [11, 19], [16, 14], [14, 12], [12, 19], [17, 18], [18, 19],
  [18, 5], [5, 7], [7, 9], [18, 6], [6, 8], [8, 10], [0, 18], [1, 2], [0, 1], [0, 2], [15, 24], [16, 25]];

function build3d() {
  const wrap = el("div", "cam three");
  const canvas = el("canvas", "three-cv");
  const label = el("div", "cam-label", "3D · uncalibrated · drag to rotate, scroll to zoom");
  const jump = el("button", "jump3d", "Jump to the 3D window ▸");
  wrap.append(canvas, label, jump);
  const fr = S.three.frames;
  // orbit around the middle of where people are, at chest height
  const pts = [];
  for (let i = 0; i < fr.length; i += 10) for (const p of fr[i]) for (const q of p.pts) if (q) pts.push(q);
  const mid = [0, 2].map((k) => pts.reduce((s, q) => s + q[k], 0) / Math.max(pts.length, 1));
  const v = { wrap, canvas, label, jump, yaw: 0.6, pitch: 0.35, dist: 3.4, target: [mid[0], 0.9, mid[1]], drag: null };
  canvas.addEventListener("pointerdown", (e) => { v.drag = [e.clientX, e.clientY, v.yaw, v.pitch]; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!v.drag) return;
    v.yaw = v.drag[2] - (e.clientX - v.drag[0]) * 0.01;
    v.pitch = Math.max(0.05, Math.min(1.5, v.drag[3] + (e.clientY - v.drag[1]) * 0.01));
  });
  canvas.addEventListener("pointerup", () => { v.drag = null; });
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); v.dist = Math.max(2, Math.min(14, v.dist * (1 + e.deltaY * 0.001))); }, { passive: false });
  jump.onclick = () => seek(S.three.t0 + 1);
  return v;
}

function draw3d(t) {
  const v = S.view3d, cv = v.canvas, ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
  const dpr = devicePixelRatio || 1;
  ctx.fillStyle = "#0b0e12"; ctx.fillRect(0, 0, W, H);
  const T = S.three, g = Math.round((t - T.t0) * T.hz), inside = g >= 0 && g < T.frames.length;
  v.jump.style.display = inside ? "none" : "";
  const eye = [v.target[0] + v.dist * Math.cos(v.pitch) * Math.sin(v.yaw), v.target[1] + v.dist * Math.sin(v.pitch),
    v.target[2] + v.dist * Math.cos(v.pitch) * Math.cos(v.yaw)];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = (a) => { const l = Math.hypot(...a); return [a[0] / l, a[1] / l, a[2] / l]; };
  const fwd = norm(sub(v.target, eye)), right = norm(cross(fwd, [0, 1, 0])), up = cross(right, fwd);
  const f = H * 1.1;
  const P = (p) => { const d = sub(p, eye), z = dot(d, fwd); return z < 0.1 ? null : [W / 2 + f * dot(d, right) / z, H / 2 - f * dot(d, up) / z, z]; };
  const line = (a, b) => { const A = P(a), B = P(b); if (A && B) { ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.stroke(); } };
  // floor grid, 1 m
  ctx.strokeStyle = "#232a33"; ctx.lineWidth = dpr;
  const gx = Math.round(v.target[0]), gz = Math.round(v.target[2]);
  for (let k = -4; k <= 4; k++) {
    line([gx + k, 0, gz - 4], [gx + k, 0, gz + 4]);
    line([gx - 4, 0, gz + k], [gx + 4, 0, gz + k]);
  }
  // cameras: a small pyramid along their view direction
  ctx.strokeStyle = "#e05d5d"; ctx.fillStyle = "#e05d5d"; ctx.font = `${11 * dpr}px system-ui`;
  for (const c of T.cameras) {
    const R = c.R, C = c.pos, s = 0.35, hw = s * (c.w / 2) / c.f, hh = s * (c.h / 2) / c.f;
    const corner = (sx, sy) => [0, 1, 2].map((k) => C[k] + R[2][k] * s + R[0][k] * sx * hw + R[1][k] * sy * hh);
    const cs = [corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)];
    for (const q of cs) line(C, q);
    for (let k = 0; k < 4; k++) line(cs[k], cs[(k + 1) % 4]);
    const L = P(C); if (L) ctx.fillText(`camera ${c.camera}`, L[0] + 6, L[1] - 6);
    line(C, [C[0], 0, C[2]]);
  }
  if (inside) {
    for (const p of T.frames[g]) {
      const col = p.who ? roleColor(p.who) : p.id === 0 ? ROLE_COLORS.PATIENT : ROLE_COLORS.LEARNER;
      // floor shadow
      ctx.strokeStyle = "rgba(255,255,255,.10)"; ctx.lineWidth = 2 * dpr;
      for (const [a, b] of SKEL3D) if (p.pts[a] && p.pts[b]) line([p.pts[a][0], 0, p.pts[a][2]], [p.pts[b][0], 0, p.pts[b][2]]);
      ctx.strokeStyle = col; ctx.lineWidth = 2.4 * dpr;
      for (const [a, b] of SKEL3D) if (p.pts[a] && p.pts[b]) line(p.pts[a], p.pts[b]);
      ctx.fillStyle = col;
      for (const q of p.pts) { if (!q) continue; const Q = P(q); if (Q) { ctx.beginPath(); ctx.arc(Q[0], Q[1], 2.2 * dpr, 0, 6.29); ctx.fill(); } }
      const head = p.pts[17] || p.pts[0] || p.pts[18];
      const Hd = head && P(head);
      if (Hd) { ctx.font = `600 ${11 * dpr}px system-ui`; ctx.fillText(p.who ? p.who.toLowerCase() : p.id === 0 ? "patient?" : "learner?", Hd[0] + 6, Hd[1] - 8); }
    }
  }
  ctx.fillStyle = "#7b8590"; ctx.font = `${10 * dpr}px system-ui`;
  ctx.fillText("grid: 1 m", 8, H - 8);
}

// ---------------------------------------------------------------- transcript, bookmarks

function buildTranscript() {
  const ol = $("#transcript");
  ol.innerHTML = "";
  S.items = S.utts.map((u) => {
    const li = el("li");
    li.style.borderLeftColor = roleColor(u.role);
    const who = el("span", "who", SHORT[u.role] || u.role.replace("_", " "));
    who.style.color = roleColor(u.role);
    li.append(el("span", "t", fmtShort(u.a)), who, el("span", "txt", u.text));
    li.onclick = () => seek(u.a);
    ol.append(li);
    return li;
  });
}

function buildBookmarks() {
  const box = $("#bookmarks");
  box.innerHTML = "";
  const bms = S.meta.bookmarks || [];
  box.parentElement.classList.toggle("hidden", !bms.length);
  for (const b of bms) {
    const btn = el("button", "bm");
    btn.append(el("span", "t", fmtShort(b.t)), el("span", null, b.label));
    btn.onclick = () => { seek(b.t); if (master().video.paused) togglePlay(); };
    box.append(btn);
  }
}

function buildLegend() {
  const lg = $("#legend");
  lg.innerHTML = "";
  for (const r of S.meta.roles) {
    const s = el("span");
    const i = el("i"); i.style.background = roleColor(r);
    s.append(i, document.createTextNode(r.toLowerCase().replace("_", " ")));
    lg.append(s);
  }
}

function updateTranscript(t) {
  let cur = -1;
  for (const u of S.utts) { if (u.a <= t && u.role !== "ANNOUNCEMENT") cur = u.i; if (u.a > t) break; }
  if (cur === S.lastNow) return;
  if (S.lastNow >= 0) S.items[S.lastNow].classList.remove("now");
  S.lastNow = cur;
  if (cur >= 0) {
    S.items[cur].classList.add("now");
    if ($("#follow").checked) {          // scroll the list only, never the page
      const ol = $("#transcript"), li = S.items[cur];
      ol.scrollTo({ top: li.offsetTop - ol.offsetTop - ol.clientHeight / 2 + li.clientHeight / 2, behavior: "smooth" });
    }
  }
}

// ---------------------------------------------------------------- timeline

function resizeCanvases() {
  const dpr = devicePixelRatio || 1;
  const cvs = [$("#overview"), $("#lanes")];
  if (S) for (const v of S.vids) cvs.push(v.canvas);
  if (S && S.view3d) cvs.push(S.view3d.canvas);
  for (const c of cvs) {
    const r = c.getBoundingClientRect();
    if (!r.width) continue;
    c.width = Math.round(r.width * dpr);
    if (c.id) {
      // the HTML height attribute is the CSS height; remember it once, since
      // setting c.height below overwrites that attribute (and would compound
      // by devicePixelRatio on every resize)
      c.dataset.cssH ||= c.getAttribute("height");
      c.style.height = c.dataset.cssH + "px";
      c.height = Math.round(parseFloat(c.dataset.cssH) * dpr);
    } else c.height = Math.round(r.height * dpr);
  }
}

const LABEL_W = 96;

function windowRange(t) {
  const z = parseFloat($("#zoom").value), D = S.meta.duration_s;
  if (!z || z >= D) return [0, D];
  let a = t - z / 2;
  a = Math.max(0, Math.min(a, D - z));
  return [a, a + z];
}

function drawOverview(t) {
  const c = $("#overview"), ctx = c.getContext("2d"), dpr = devicePixelRatio || 1;
  const W = c.width, H = c.height, D = S.meta.duration_s, L = LABEL_W * dpr;
  ctx.clearRect(0, 0, W, H);
  const x = (s) => L + (s / D) * (W - L);
  ctx.fillStyle = "#9aa3ad"; ctx.font = `${11 * dpr}px system-ui`;
  ctx.fillText("whole encounter", 4 * dpr, 16 * dpr);
  const roles = S.meta.roles, rh = (H - 14 * dpr) / roles.length;
  roles.forEach((r, i) => {
    ctx.fillStyle = roleColor(r);
    for (const u of S.utts) if (u.role === r) ctx.fillRect(x(u.a), i * rh, Math.max(1, x(u.b) - x(u.a)), rh - 1);
  });
  // minute ticks
  ctx.fillStyle = "#6b7480";
  for (let m = 0; m <= D / 60; m += (D > 1800 ? 10 : 2)) {
    ctx.fillRect(x(m * 60), H - 12 * dpr, 1, 4 * dpr);
    ctx.fillText(`${m}m`, x(m * 60) + 2, H - 2 * dpr);
  }
  for (const b of S.meta.bookmarks || []) {
    ctx.fillStyle = "#ffe08a";
    ctx.beginPath(); ctx.moveTo(x(b.t) - 4 * dpr, 0); ctx.lineTo(x(b.t) + 4 * dpr, 0); ctx.lineTo(x(b.t), 6 * dpr); ctx.fill();
  }
  if (S.three) {
    const t0 = S.three.t0, t1 = t0 + S.three.frames.length / S.three.hz;
    ctx.fillStyle = "rgba(199,146,234,.85)";
    ctx.fillRect(x(t0), H - 14 * dpr, x(t1) - x(t0), 3 * dpr);
  }
  const [a, b] = windowRange(t);
  ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = dpr;
  ctx.strokeRect(x(a), 0.5, Math.max(2, x(b) - x(a)), H - 13 * dpr);
  ctx.fillStyle = "#fff"; ctx.fillRect(x(t) - dpr, 0, 2 * dpr, H - 12 * dpr);
}

function drawLanes(t) {
  const c = $("#lanes"), ctx = c.getContext("2d"), dpr = devicePixelRatio || 1;
  const W = c.width, H = c.height, L = LABEL_W * dpr;
  const [a, b] = windowRange(t);
  const x = (s) => L + ((s - a) / (b - a)) * (W - L);
  ctx.clearRect(0, 0, W, H);
  ctx.font = `${11 * dpr}px system-ui`;
  ctx.save();
  ctx.beginPath(); ctx.rect(L, 0, W - L, H); ctx.clip();
  let y = 0;
  const labels = [];
  const lane = (name, h, sub) => {
    ctx.fillStyle = "#1e232b"; ctx.fillRect(L, y, W - L, h);
    labels.push([name, sub, y]);
    const top = y; y += h + 6 * dpr; return top;
  };
  const roles = S.meta.roles;
  const talkRoles = roles.filter((r) => r !== "ANNOUNCEMENT");

  // talk blocks + pauses
  const rowH = 13 * dpr, tH = rowH * roles.length + 14 * dpr;
  let top = lane("Who talks", tH, "pauses ≥ 2 s");
  for (const [p0, p1] of S.pauses) {
    if (p1 < a || p0 > b) continue;
    ctx.fillStyle = "rgba(255,224,138,.16)"; ctx.fillRect(x(p0), top, x(p1) - x(p0), tH);
    const wpx = x(p1) - x(p0);
    if (wpx > 34 * dpr) { ctx.fillStyle = "#ffe08a"; ctx.fillText(`${(p1 - p0).toFixed(1)} s`, x(p0) + 3 * dpr, top + tH - 3 * dpr); }
  }
  roles.forEach((r, i) => {
    ctx.fillStyle = roleColor(r);
    for (const u of S.utts) if (u.role === r && u.b > a && u.a < b) ctx.fillRect(x(u.a), top + i * rowH + 1, Math.max(1, x(u.b) - x(u.a)), rowH - 2);
  });

  // speech rate per utterance
  top = lane("Speech rate", 46 * dpr, "words / s");
  const rate = (u) => u.text.split(/\s+/).filter((w) => /[a-z]/i.test(w) && !w.startsWith("[")).length / Math.max(u.b - u.a, 0.3);
  for (const u of S.utts) {
    if (u.role === "ANNOUNCEMENT" || u.b < a || u.a > b || u.b - u.a < 0.8) continue;
    const r = Math.min(rate(u), 6), yy = top + 46 * dpr - (r / 6) * 44 * dpr;
    ctx.fillStyle = roleColor(u.role); ctx.fillRect(x(u.a), yy - dpr, Math.max(2, x(u.b) - x(u.a)), 3 * dpr);
  }

  // pitch
  const P = S.lanes.prosody;
  if (P) {
    const ph = 86 * dpr;
    top = lane("Pitch", ph, "Hz, log scale");
    const lo = Math.log(70), hi = Math.log(400), py = (hz) => top + ph - ((Math.log(hz) - lo) / (hi - lo)) * ph;
    ctx.fillStyle = "#3a414b";
    for (const hz of [100, 200, 300]) { ctx.fillRect(L, py(hz), W - L, 1); ctx.fillText(`${hz}`, L + 2, py(hz) - 2); }
    const i0 = Math.max(0, Math.floor(a * P.hz)), i1 = Math.min(Math.ceil(b * P.hz), 1e9);
    const step = Math.max(1, Math.floor((i1 - i0) / ((W - L) / dpr)));
    for (const r of talkRoles) {
      const arr = P.pitch_hz[r]; if (!arr) continue;
      ctx.fillStyle = roleColor(r);
      for (let i = i0; i < Math.min(i1, arr.length); i += step) {
        if (arr[i] == null) continue;
        ctx.fillRect(x(i / P.hz) - dpr, py(arr[i]) - dpr, 2.5 * dpr, 2.5 * dpr);
      }
    }
    // loudness
    const lh = 46 * dpr;
    top = lane("Loudness", lh, "dB, relative");
    const [l0, l1] = S.scales.loud, ly = (db) => top + lh - Math.max(0, Math.min(1, (db - l0) / (l1 - l0))) * lh;
    ctx.strokeStyle = "#aeb6c0"; ctx.lineWidth = dpr; ctx.beginPath();
    let pen = false;
    for (let i = i0; i < Math.min(i1, P.loudness_db.length); i += step) {
      const v = P.loudness_db[i];
      if (v == null) { pen = false; continue; }
      pen ? ctx.lineTo(x(i / P.hz), ly(v)) : ctx.moveTo(x(i / P.hz), ly(v)); pen = true;
    }
    ctx.stroke();
  }

  // movement on the main camera
  const cam = S.vids[S.mainIdx].cam.camera, M = S.lanes.motion[cam] || {}, hz = S.meta.motion_hz;
  const mh = 86 * dpr, mmax = S.scales.motion[cam] || 1;
  top = lane("Movement", mh, `camera ${cam}`);
  const my = (v) => top + mh - Math.min(1, v / mmax) * (mh - 2 * dpr);
  for (const [r, arr] of Object.entries(M)) {
    ctx.strokeStyle = ROLE_COLORS[r] || "#ccc"; ctx.lineWidth = 1.6 * dpr; ctx.beginPath();
    let pen = false;
    const i0 = Math.max(0, Math.floor(a * hz)), i1 = Math.min(arr.length, Math.ceil(b * hz) + 1);
    for (let i = i0; i < i1; i++) {
      const v = arr[i];
      if (v == null) { pen = false; continue; }
      pen ? ctx.lineTo(x(i / hz), my(v)) : ctx.moveTo(x(i / hz), my(v)); pen = true;
    }
    ctx.stroke();
  }

  // head direction (a gaze proxy), on whichever camera has it
  const heads = Object.entries(S.lanes.head || {});
  if (heads.length) {
    const [hcam, H2] = heads[0], hroles = Object.keys(H2), rh = 13 * dpr, hh = rh * hroles.length;
    top = lane("Head direction", hh, hcam === "3D" ? "3D stretch only" : `camera ${hcam}`);
    hroles.forEach((r, j) => {
      const sq = H2[r];
      const i0 = Math.max(0, Math.floor(a * hz)), i1 = Math.min(sq.length, Math.ceil(b * hz) + 1);
      for (let i = i0; i < i1; i++) {
        const c = sq[i];
        if (c === "0") continue;
        ctx.fillStyle = c === "1" ? roleColor(r) : c === "2" ? "#5c6672" : "#c0392b";
        ctx.fillRect(x(i / hz), top + j * rh + 1, Math.max(1, x((i + 1) / hz) - x(i / hz)), rh - 2);
      }
    });
  }

  // camera views
  top = lane("Camera view", 18 * dpr);
  const vc = { Baseline: "#3d4f66", Bed: "#4b3d66", Chair: "#3d6650", Sink: "#665a3d", PC: "#663d4a", User: "#55603a", moving: "#2b2f36" };
  for (const s of S.vids[S.mainIdx].cam.views) {
    if (s.t1 < a || s.t0 > b) continue;
    ctx.fillStyle = vc[s.label] || "#46505c";
    ctx.fillRect(x(Math.max(s.t0, a)), top + 2, x(Math.min(s.t1, b)) - x(Math.max(s.t0, a)) - 1, 14 * dpr);
    ctx.fillStyle = "#d8dde3"; ctx.fillText(s.label, x(Math.max(s.t0, a)) + 4 * dpr, top + 13 * dpr);
  }

  // time axis + playhead
  ctx.fillStyle = "#7b8590";
  const span = b - a, tick = span <= 40 ? 5 : span <= 150 ? 15 : span <= 400 ? 60 : 120;
  for (let s = Math.ceil(a / tick) * tick; s <= b; s += tick) {
    ctx.fillRect(x(s), y - 4 * dpr, 1, 4 * dpr); ctx.fillText(fmtShort(s), x(s) + 2, y + 8 * dpr);
  }
  ctx.fillStyle = "#fff"; ctx.fillRect(x(t) - dpr, 0, 2 * dpr, y);
  ctx.restore();
  for (const [name, sub, ly] of labels) {
    ctx.fillStyle = "#c9ced5"; ctx.fillText(name, 4 * dpr, ly + 13 * dpr);
    if (sub) { ctx.fillStyle = "#7b8590"; ctx.fillText(sub, 4 * dpr, ly + 26 * dpr); }
  }
}

// ---------------------------------------------------------------- loop, controls

let lastDraw = 0;
function tick(ts) {
  if (S && !$("#one").classList.contains("hidden")) {
    const t = now();
    syncSlaves(t);
    drawSkeletons(t);
    if (S.view3d) draw3d(t);
    $("#clock").textContent = fmt(t);
    $("#playBtn").textContent = master().video.paused ? "▶" : "❚❚";
    updateTranscript(t);
    if (ts - lastDraw > 30) { drawOverview(t); drawLanes(t); lastDraw = ts; }
  }
  requestAnimationFrame(tick);
}

function wireControls() {
  $("#playBtn").onclick = togglePlay;
  $("#speed").onchange = () => { if (S) for (const v of S.vids) v.video.playbackRate = parseFloat($("#speed").value); };
  $("#pixToggle").onchange = (e) => setPixelated(e.target.checked);
  $("#swapBtn").onclick = () => { if (S && S.vids.length > 1) { S.mainIdx = (S.mainIdx + 1) % S.vids.length; layoutVideos(); } };
  $("#overview").onclick = (e) => {
    const r = e.target.getBoundingClientRect();
    seek(((e.clientX - r.left - LABEL_W) / (r.width - LABEL_W)) * S.meta.duration_s);
  };
  $("#lanes").onclick = (e) => {
    const r = e.target.getBoundingClientRect(), [a, b] = windowRange(now());
    const fx = (e.clientX - r.left - LABEL_W) / (r.width - LABEL_W);
    if (fx >= 0) seek(a + fx * (b - a));
  };
  window.addEventListener("resize", () => { resizeCanvases(); drawAllStrips(); });
  window.addEventListener("keydown", (e) => {
    if (!S || $("#one").classList.contains("hidden") || e.target.tagName === "SELECT") return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    if (e.code === "ArrowLeft") seek(now() - (e.shiftKey ? 30 : 5));
    if (e.code === "ArrowRight") seek(now() + (e.shiftKey ? 30 : 5));
  });
}

// ---------------------------------------------------------------- all encounters

let SELECTED = null;

function buildAll() {
  $("#sortBy").onchange = drawAllStrips;
  $("#humanOnly").onchange = drawAllStrips;
  $("#openExplorer").onclick = () => {
    if (!SELECTED) return;
    $("#caseSelect").value = SELECTED.case;
    showTab("one");
    loadCase(SELECTED.case);
  };
}

function drawStrip(canvas, c, maxD, hover) {
  const dpr = devicePixelRatio || 1, r = canvas.getBoundingClientRect();
  canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
  const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height;
  const x = (s) => (s / maxD) * W;
  ctx.fillStyle = "#1e232b"; ctx.fillRect(0, 0, x(c.duration_s), H);
  const n = Math.min(c.speakers.length, 4);
  const rh = H / Math.max(n, 1);
  for (const [a, d, k] of c.turns) {
    if (k === -1) {
      ctx.fillStyle = "rgba(138,143,152,.55)"; ctx.fillRect(x(a), 0, Math.max(1, x(d)), H);
    } else if (k >= 0) {
      ctx.fillStyle = RANK_COLORS[k % RANK_COLORS.length];
      const row = Math.min(k, n - 1);
      ctx.fillRect(x(a), row * rh, Math.max(1, x(d)), rh - (H > 30 ? 2 : 0.5));
    }
  }
}

function sortedCases() {
  const key = $("#sortBy").value, human = $("#humanOnly").checked;
  let cs = OVERVIEW.cases.filter((c) => !human || c.human === "gold" || c.human === "pass1");
  const num = (c) => key in c ? c[key] : (c.measures[key] ?? -1);
  if (key === "room") cs.sort((a, b) => (+a.room || 99) - (+b.room || 99) || a.case.localeCompare(b.case));
  else if (key === "case") cs.sort((a, b) => a.case.localeCompare(b.case));
  else cs.sort((a, b) => num(b) - num(a));
  return cs;
}

function drawAllStrips() {
  if (!OVERVIEW || $("#all").classList.contains("hidden")) return;
  const cs = sortedCases();
  const maxD = Math.max(...OVERVIEW.cases.map((c) => c.duration_s));
  const box = $("#strips");
  box.innerHTML = "";
  for (const c of cs) {
    const row = el("div", "strip-row" + (SELECTED && SELECTED.case === c.case ? " sel" : ""));
    const id = el("span", "c-id");
    id.append(document.createTextNode(c.case + " "), el("span", "room", `· rm ${c.room}`));
    if (c.human) id.append(el("span", "h " + c.human, c.human === "pass1" ? "human" : c.human));
    const cv = el("canvas");
    row.append(id, cv);
    const m = c.measures;
    for (const v of [m.speech_pct, m.overlap_pct, m.longest_silence_s != null ? fmtShort(m.longest_silence_s) : "", m.turns_per_min, m.top_speaker_pct])
      row.append(el("span", "c-num", v ?? ""));
    row.title = `${c.case}, room ${c.room}, ${fmtShort(c.duration_s)} long, ${c.speakers.length} voices`;
    row.onclick = () => selectCase(c);
    box.append(row);
    drawStrip(cv, c, maxD);
  }
  const axis = $("#axis");
  axis.innerHTML = "";
  for (let m = 0; m <= maxD / 60; m += 10) {
    const s = el("span", null, `${m}m`); s.style.left = `${(m * 60 / maxD) * 100}%`; axis.append(s);
  }
  if (SELECTED) drawDetail();
}

function selectCase(c) {
  SELECTED = c;
  document.querySelectorAll(".strip-row").forEach((r) => r.classList.toggle("sel", r.firstChild.textContent.startsWith(c.case)));
  drawDetail();
}

function drawDetail() {
  const c = SELECTED;
  $("#detail").classList.remove("hidden");
  $("#detailTitle").textContent = `Case ${c.case} · room ${c.room} · ${fmtShort(c.duration_s)} · machine transcript`;
  $("#openExplorer").classList.toggle("hidden", !BUILT[c.case]);
  drawStrip($("#detailStrip"), c, c.duration_s);
  const ol = $("#detailTranscript");
  ol.innerHTML = "";
  for (const [a, k, text] of c.transcript) {
    const li = el("li");
    const col = k === -1 ? "#8a8f98" : RANK_COLORS[Math.max(k, 0) % RANK_COLORS.length];
    li.style.borderLeftColor = col;
    const who = el("span", "who", k === -1 ? "announce" : k >= 0 ? `voice ${k + 1}` : "?");
    who.style.color = col;
    li.append(el("span", "t", fmtShort(a)), who, el("span", "txt", text));
    ol.append(li);
  }
}

init();
