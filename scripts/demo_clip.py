#!/usr/bin/env python
"""Make a short demo video of a case: both cameras side by side, time-synced,
with 2D pose drawn on, faces blurred.

    python scripts/demo_clip.py 261498                      # picks a lively 45 s
    python scripts/demo_clip.py 261498 --start 2:25 --dur 30
    python scripts/demo_clip.py 261498 --audio --labels none --out ~/demo.mp4

md-pose env (OpenCV + ffmpeg). Needs the case's pose files; uses its sync
offsets and camera views when they exist. Writes an H.264 .mp4 that plays in
any browser or slide deck (default: data/demos/<case>_<start>.mp4) and prints
where it is.

What you see:
  - title bar: case, room, the time on the case's reference clock
  - each camera, with its preset name when the view has been named
  - skeletons, coloured by a *guessed* role ("patient?" = the person who stays
    put, "learner?" = the most visible other person) -- position-based
    guesses, not identification. `--labels none` draws everyone alike.

Privacy, please read before sharing a clip:
  - Faces are blurred by default, from each person's head keypoints. It's
    best effort: a face the pose model missed in a frame is NOT blurred, and
    faces seen in the window/mirror often aren't detected at all. Watch the
    clip before it leaves secure storage. `--no-blur` turns it off.
  - Sound is off unless you pass `--audio`: learners say their names.
  - Mirror reflections can show up as extra people.

If you don't give --start, the clip is the --dur seconds with the most people
in view while both cameras hold still (from the views stage).
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from multidata import pose  # noqa: E402
from multidata.paths import DATA_DIR, MANIFEST_PATH  # noqa: E402
from multidata.tools import which  # noqa: E402

KPT_THR = 0.3
MIN_KPTS = 5
FPS = 30
PANEL_W, PANEL_H, BAR = 960, 540, 44
COLORS = {"patient?": (60, 170, 255), "learner?": (255, 160, 40),   # BGR
          "other": (150, 150, 150), "person": (80, 220, 120)}
# HALPE_26 links (pose.KEYPOINTS): legs to mid-hip, spine, arms from the neck, face, feet
LINKS = [(15, 13), (13, 11), (11, 19), (16, 14), (14, 12), (12, 19), (17, 18), (18, 19),
         (18, 5), (5, 7), (7, 9), (18, 6), (6, 8), (8, 10), (1, 2), (0, 1), (0, 2),
         (1, 3), (2, 4), (15, 20), (15, 22), (15, 24), (16, 21), (16, 23), (16, 25)]
HEAD = [0, 1, 2, 3, 4, 17]          # nose, eyes, ears, head top


# --- loading ---------------------------------------------------------------

def load_case(case_id, cameras, db):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    case = c.execute("SELECT room_number, audio_camera FROM cases WHERE case_id = ?",
                     (case_id,)).fetchone()
    if not case:
        sys.exit(f"case {case_id} isn't in the manifest")
    room, audio_cam = case
    rows = c.execute("SELECT camera, filepath, sync_offset_s FROM videos WHERE case_id = ? "
                     "ORDER BY camera", (case_id,)).fetchall()
    if cameras:
        rows = [r for r in rows if r[0] in cameras]
    if not rows:
        sys.exit(f"case {case_id} has no videos" + (f" for cameras {cameras}" if cameras else ""))
    labels = dict(((cam, v), lab) for cam, v, lab in
                  c.execute("SELECT camera, view_id, label FROM camera_views"))
    cams = []
    for cam, path, offset in rows:
        pose_path = DATA_DIR / "pose" / case_id / f"{cam}.npz"
        if not pose_path.exists():
            sys.exit(f"no pose for {case_id}/{cam} yet -- run: "
                     f"python scripts/run_stage.py pose --only {case_id} --accel-device cuda")
        e = pose.load(pose_path)
        if offset is None:
            print(f"note: camera {cam} has no sync offset (sync failed or not run); "
                  "its panel may be up to ~1 s off")
        views_path = DATA_DIR / "views" / case_id / f"{cam}.json"
        stretches = json.loads(views_path.read_text())["stretches"] if views_path.exists() else []
        cams.append({"camera": cam, "video": ROOT / path, "offset": offset or 0.0,
                     "kp": e["keypoint"], "sc": e["keypoint_score"],
                     "t_ref": e["frame_ms"] / 1000.0 + (offset or 0.0),
                     "frame_ms": e["frame_ms"], "stretches": stretches,
                     "labels": {v: labels.get((cam, v), "") for v in {s["view"] for s in stretches}}})
    return room, audio_cam, cams


# --- choosing the clip -----------------------------------------------------

def people_count(sc):
    """(T,) number of confidently detected people per frame."""
    return ((sc > KPT_THR).sum(axis=2) >= MIN_KPTS).sum(axis=0)


def pick_start(cams, dur):
    """Start (reference seconds) of the `dur`-second window with the most
    people in view, preferring windows where every camera holds still."""
    end = min(c["t_ref"][-1] for c in cams)
    begin = max(c["t_ref"][0] for c in cams)
    secs = np.arange(int(np.ceil(begin)), int(end))
    if len(secs) <= dur:
        return float(begin)
    score = np.zeros(len(secs))
    steady = np.ones(len(secs), bool)
    for c in cams:
        n = np.minimum(people_count(c["sc"]), 3).astype(float)
        idx = np.clip(np.searchsorted(c["t_ref"], secs), 0, len(n) - 1)
        score += n[idx]
        held = np.zeros(len(secs), bool)
        for s in c["stretches"]:
            if s["view"] and s["ref_t0"] is not None:
                held |= (secs >= s["ref_t0"]) & (secs < s["ref_t1"])
        if c["stretches"]:
            steady &= held
    win = int(dur)
    total = np.convolve(score, np.ones(win), "valid")
    ok = np.convolve(steady.astype(float), np.ones(win), "valid") >= win
    if ok.any():
        total = np.where(ok, total, -1)
    return float(secs[int(np.argmax(total))])


def parse_time(s):
    """'145', '2:25' or '1:02:25' -> seconds."""
    parts = [float(p) for p in str(s).split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def clock(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:04.1f}" if t >= 3600 \
        else f"{int(t // 60)}:{t % 60:04.1f}"


# --- roles, drawing --------------------------------------------------------

def anchor(c, lo, hi):
    """Where the person who stays put is: the densest 40-px cell of people's
    body centres over the clip (the patient mostly sits still)."""
    kp, sc = c["kp"][:, lo:hi:5], c["sc"][:, lo:hi:5]
    vis = sc[..., :17] > KPT_THR
    ok = vis.sum(axis=2) >= 4
    if not ok.any():
        return None
    centres = (kp[..., :17, :] * vis[..., None]).sum(axis=2) / np.maximum(vis.sum(axis=2), 1)[..., None]
    pts = centres[ok]
    H, xe, ye = np.histogram2d(pts[:, 0], pts[:, 1], bins=[np.arange(0, 2001, 40), np.arange(0, 1201, 40)])
    i, j = np.unravel_index(H.argmax(), H.shape)
    return np.array([xe[i] + 20, ye[j] + 20])


def roles(kp, sc, anch, mode):
    """{slot: label} for one frame."""
    present = []
    for s in range(kp.shape[0]):
        vis = sc[s, :17] > KPT_THR
        if vis.sum() >= 4:
            present.append((s, kp[s, :17][vis].mean(0), sc[s, :17][vis].mean()))
    if mode == "none" or anch is None:
        return {s: "person" for s, _, _ in present}
    out = {}
    if present:
        p = min(present, key=lambda r: np.linalg.norm(r[1] - anch))
        out[p[0]] = "patient?"
        rest = sorted((r for r in present if r[0] != p[0]), key=lambda r: -r[2])
        for i, r in enumerate(rest):
            out[r[0]] = "learner?" if i == 0 else "other"
    return out


def blur_faces(frame, kp, sc):
    """Blur an ellipse around each detected person's head keypoints."""
    h, w = frame.shape[:2]
    for s in range(kp.shape[0]):
        m = sc[s, HEAD] > 0.1
        if m.sum() < 2:
            continue
        pts = kp[s, HEAD][m]
        cx, cy = pts.mean(0)
        r = max(float(np.linalg.norm(pts - (cx, cy), axis=1).max()) * 2.8, 22.0)
        x0, y0 = int(max(cx - r, 0)), int(max(cy - r * 1.2, 0))
        x1, y1 = int(min(cx + r, w)), int(min(cy + r * 1.2, h))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        roi = frame[y0:y1, x0:x1]
        # pixelate (shrink, then enlarge): cheap, and hides a face at least as
        # well as a large Gaussian blur
        small = cv2.resize(roi, (max((x1 - x0) // 24, 1), max((y1 - y0) // 24, 1)),
                           interpolation=cv2.INTER_AREA)
        blurred = cv2.GaussianBlur(cv2.resize(small, (x1 - x0, y1 - y0),
                                              interpolation=cv2.INTER_LINEAR), (9, 9), 0)
        mask = np.zeros(roi.shape[:2], np.uint8)
        cv2.ellipse(mask, (int(cx) - x0, int(cy) - y0), (int(r), int(r * 1.2)), 0, 0, 360, 255, -1)
        roi[mask > 0] = blurred[mask > 0]
    return frame


def draw(frame, kp, sc, labels):
    for s, lab in labels.items():
        col, k, c = COLORS[lab], kp[s], sc[s]
        for a, b in LINKS:
            if c[a] > KPT_THR and c[b] > KPT_THR:
                cv2.line(frame, tuple(map(int, k[a])), tuple(map(int, k[b])), col, 2, cv2.LINE_AA)
        for v in range(len(c)):
            if c[v] > KPT_THR and v not in HEAD:
                cv2.circle(frame, tuple(map(int, k[v])), 3, col, -1, cv2.LINE_AA)
        vis = c[:17] > KPT_THR
        if lab != "person" and vis.any():
            x, y = k[:17][vis, 0].min(), k[:17][vis, 1].min()
            cv2.putText(frame, lab, (int(x), max(int(y) - 10, 16)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, col, 2, cv2.LINE_AA)
    return frame


def text(img, s, xy, scale=0.6, col=(235, 235, 235), bold=1):
    cv2.putText(img, s, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, col, bold, cv2.LINE_AA)


def view_label(c, t):
    for s in c["stretches"]:
        if s["ref_t0"] is not None and s["ref_t0"] <= t < s["ref_t1"]:
            return c["labels"].get(s["view"]) or ("moving" if not s["view"] else "")
    return ""


class Reader:
    """Hands back the video frame for a pose frame index, reading forward."""

    def __init__(self, c, first):
        self.cap = cv2.VideoCapture(str(c["video"]))
        self.ms = c["frame_ms"]
        self.cap.set(cv2.CAP_PROP_POS_MSEC, max(self.ms[first] - 3000, 0))
        self.cur_ms, self.frame = -1.0, None

    def get(self, i):
        while self.cur_ms < self.ms[i] - 1:
            ok, f = self.cap.read()
            if not ok:
                break
            self.cur_ms, self.frame = self.cap.get(cv2.CAP_PROP_POS_MSEC), f
        return self.frame


# --- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("case", help="case id, e.g. 261498")
    ap.add_argument("--start", help="clip start on the case clock: seconds, m:ss or h:mm:ss "
                                    "(default: pick a lively stretch)")
    ap.add_argument("--dur", type=float, default=45, help="clip length in seconds (default 45)")
    ap.add_argument("--out", help="output .mp4 (default data/demos/<case>_<start>.mp4)")
    ap.add_argument("--cameras", nargs="+", help="only these cameras (default: all of the case's)")
    ap.add_argument("--labels", choices=["roles", "none"], default="roles",
                    help="roles: colour by guessed patient/learner (default); none: all alike")
    ap.add_argument("--no-blur", action="store_true", help="don't blur faces")
    ap.add_argument("--audio", action="store_true", help="include sound (names are audible)")
    ap.add_argument("--manifest", default=MANIFEST_PATH)
    args = ap.parse_args()

    room, audio_cam, cams = load_case(args.case, args.cameras, args.manifest)
    start = parse_time(args.start) if args.start else pick_start(cams, args.dur)
    n = int(args.dur * FPS)
    ticks = start + np.arange(n) / FPS
    for c in cams:
        j = np.clip(np.searchsorted(c["t_ref"], ticks), 1, len(c["t_ref"]) - 1)
        j -= (ticks - c["t_ref"][j - 1]) < (c["t_ref"][j] - ticks)
        c["idx"] = j
        c["gap"] = np.abs(c["t_ref"][j] - ticks) > 0.2      # camera dropped frames here
        c["anchor"] = anchor(c, int(j[0]), int(j[-1]) + 1)
        c["reader"] = Reader(c, int(j[0]))

    out = Path(args.out).expanduser() if args.out else \
        DATA_DIR / "demos" / f"{args.case}_{clock(start).replace(':', '-').split('.')[0]}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    W = PANEL_W * len(cams)
    footer = ("faces blurred (best effort; mirrors often missed: check before sharing)"
              if not args.no_blur
              else "FACES NOT BLURRED") + \
             ("  |  roles are guesses from position" if args.labels == "roles" else "")
    with tempfile.TemporaryDirectory() as tmp:
        silent = Path(tmp) / "video.mp4"
        writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), FPS,
                                 (W, PANEL_H + 2 * BAR))
        for i in range(n):
            panels = []
            for c in cams:
                t = int(c["idx"][i])
                frame = c["reader"].get(t)
                frame = np.zeros((720, 1280, 3), np.uint8) if frame is None else frame.copy()
                kp, sc = c["kp"][:, t], c["sc"][:, t]
                if not c["gap"][i]:
                    if not args.no_blur:
                        frame = blur_faces(frame, kp, sc)
                    frame = draw(frame, kp, sc, roles(kp, sc, c["anchor"], args.labels))
                scale = PANEL_W / frame.shape[1]
                frame = cv2.resize(frame, (PANEL_W, int(frame.shape[0] * scale)))[:PANEL_H]
                if frame.shape[0] < PANEL_H:
                    frame = np.vstack([frame, np.zeros((PANEL_H - frame.shape[0], PANEL_W, 3), np.uint8)])
                cv2.rectangle(frame, (0, PANEL_H - 30), (PANEL_W, PANEL_H), (0, 0, 0), -1)
                vl = view_label(c, ticks[i])
                text(frame, f"camera {c['camera']}" + (f"  -  {vl}" if vl else "")
                     + ("  -  frames missing here" if c["gap"][i] else ""), (10, PANEL_H - 9))
                panels.append(frame)
            top = np.full((BAR, W, 3), 30, np.uint8)
            text(top, f"Case {args.case}  -  room {room}  -  {clock(ticks[i])}", (14, 29), 0.75, bold=2)
            bottom = np.full((BAR, W, 3), 30, np.uint8)
            x = 14
            if args.labels == "roles":
                for lab in ("patient?", "learner?", "other"):
                    cv2.circle(bottom, (x + 7, 22), 7, COLORS[lab], -1)
                    text(bottom, lab, (x + 20, 28), 0.55, COLORS[lab])
                    x += 120
            text(bottom, footer, (x + 10, 28), 0.5, (190, 190, 190))
            writer.write(np.vstack([top, np.hstack(panels), bottom]))
            if i % (FPS * 5) == 0:
                print(f"\r{100 * i / n:3.0f}%", end="", flush=True)
        writer.release()
        print("\r100%")
        cmd = [which("ffmpeg"), "-v", "error", "-y", "-i", str(silent)]
        ref = next((c for c in cams if c["camera"] == audio_cam), cams[0])
        if args.audio:
            cmd += ["-ss", f"{start - ref['offset']:.3f}", "-t", f"{args.dur:.3f}", "-i", str(ref["video"]),
                    "-map", "0:v", "-map", "1:a", "-c:a", "aac"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart",
                str(out)]
        subprocess.run(cmd, check=True)
    print(f"wrote {out}  ({args.dur:.0f} s from {clock(start)}, "
          f"{'with' if args.audio else 'no'} sound, faces {'NOT ' if args.no_blur else ''}blurred)")


if __name__ == "__main__":
    main()
