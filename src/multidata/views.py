"""Stage: views -- where each camera is pointing, and when it moves.

Runs in the **md-pose** env (OpenCV, plus `ffmpeg`/`ffprobe` on PATH).

The exam-room cameras are PTZ and an operator steers them during sessions
(a 2026-10-30 survey found 5-20 moves an hour). A 3D calibration only holds
while a camera holds still, so this stage splits each video into **steady
stretches** and says which **view** each stretch is.

How:
  1. Decode keyframes only (~1 a second) at 320x180 gray.
  2. For each keyframe, match ORB features against the current stretch's
     reference frame and fit a similarity transform (shift, zoom, rotation)
     with RANSAC. People moving are outliers; the room's fixed structure
     carries the fit. The camera moved when the fit says so (or nothing
     matches) for PERSIST keyframes in a row that agree with each other.
     Neighbouring stretches whose views turn out the same are merged.
  3. Each stretch of MIN_VIEW_S or longer is matched against the camera's
     **view library**: the views seen before, each stored as 5 sample
     frames. A match is a median fit within the SAME_* tolerances (preset
     recall is repeatable to a few pixels). No match -> a new view id.

Ids are stable: a view keeps its id (`v1`, `v2`, ... per camera) across
cases and re-runs, because new stretches are matched against the stored
samples, never re-clustered. Process videos oldest first (run_stage does)
so ids follow first appearance. One worker only: two would race on adding
views to the library.

Outputs:
  - `data/views/<case_id>/<camera>.json`  every stretch, with file-clock and
    reference-clock times, its view id (None if shorter than MIN_VIEW_S),
    the match quality, and how far the camera moved from the previous one.
  - `data/views/library/<camera>/<view_id>.npz` (5 sample frames) and `.png`
    (the same as a strip, for people).
  - manifest `camera_views`  one row per view: label (provisional until
    someone names it), where it was first seen.
  - manifest `video_views`   seconds and stretch count per (video, view);
    "is this a preset" is a query over it (seen in 2+ cases, or 3+ times),
    not a stored flag, so it updates itself as cases arrive.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

W, H = 320, 180
PERSIST = 3                 # keyframes a change must last (~3 s)
MIN_INLIERS = 25
MOVE = (3.0, 0.015, 1.0)    # shift px (at 320 wide), |zoom-1|, rotation deg: a move
SAME = (4.0, 0.02, 1.5)     # looser: the same view recalled again
MIN_VIEW_S = 30.0           # shorter stretches are transitions, not views
N_SAMPLES = 5
PARAMS = {"size": [W, H], "persist": PERSIST, "min_inliers": MIN_INLIERS, "move": MOVE,
          "same": SAME, "min_view_s": MIN_VIEW_S, "n_samples": N_SAMPLES}

_ORB = None
_BF = None


def _tools():
    global _ORB, _BF
    if _ORB is None:
        _ORB = cv2.ORB_create(1000, fastThreshold=10)
        _BF = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    return _ORB, _BF


def _bin(name):
    """`name` from PATH, else from this Python's own env (an env used
    without `conda activate` isn't on PATH)."""
    path = shutil.which(name) or shutil.which(name, path=str(Path(sys.executable).parent))
    if not path:
        raise RuntimeError(f"{name} not found -- install ffmpeg into this env")
    return path


def keyframes(video_path):
    """(times_s, frames): keyframes only, gray, W x H. Fast: no other frame
    is decoded. Times are the file's own presentation timestamps."""
    t = subprocess.run([_bin("ffprobe"), "-v", "error", "-select_streams", "v:0",
                        "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
                        "-of", "csv=p=0", str(video_path)],
                       capture_output=True, text=True, check=True).stdout.split()
    raw = subprocess.run([_bin("ffmpeg"), "-v", "error", "-skip_frame", "nokey",
                          "-i", str(video_path), "-an", "-fps_mode", "passthrough",
                          "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W)
    times = np.array([float(x) for x in t if x != "N/A"])
    n = min(len(times), len(frames))
    return times[:n], frames[:n]


def features(img):
    orb, _ = _tools()
    return orb.detectAndCompute(img, None)


def motion(a, b):
    """Camera motion from frame a to frame b: (dx, dy, zoom, rot_deg) of the
    image centre, or None when they don't match well enough to say."""
    _, bf = _tools()
    (ka, da), (kb, db) = a, b
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return None
    m = bf.match(da, db)
    if len(m) < MIN_INLIERS:
        return None
    pa = np.float32([ka[x.queryIdx].pt for x in m])
    pb = np.float32([kb[x.trainIdx].pt for x in m])
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if M is None or inl is None or int(inl.sum()) < MIN_INLIERS:
        return None
    zoom = float(np.hypot(M[0, 0], M[1, 0]))
    rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    c = np.array([W / 2, H / 2])
    d = M[:, :2] @ c + M[:, 2] - c
    return float(d[0]), float(d[1]), zoom, rot


def _exceeds(p, tol):
    shift, zoom, rot = tol
    return p is None or np.hypot(p[0], p[1]) > shift or abs(p[2] - 1) > zoom or abs(p[3]) > rot


def median_motion(fa, fb):
    """Median motion over all pairs of two sets of frame features, or None
    when fewer than a fifth of the pairs match. Robust to people blocking
    some frames."""
    fits = [p for a in fa for b in fb if (p := motion(a, b)) is not None]
    if len(fits) < max(1, len(fa) * len(fb) // 5):
        return None
    return tuple(float(x) for x in np.median(np.array(fits), axis=0))


def segment(feats):
    """Index boundaries [0, b1, ..., n] of steady stretches."""
    bounds, ref, pending = [0], 0, []
    for i in range(1, len(feats)):
        if not _exceeds(motion(feats[ref], feats[i]), MOVE):
            pending = []
            continue
        pending.append(i)
        if len(pending) >= PERSIST and all(
                not _exceeds(motion(feats[pending[-1]], feats[j]), MOVE)
                for j in pending[-PERSIST:-1]):
            bounds.append(pending[-PERSIST])
            ref, pending = pending[-1], []
    bounds.append(len(feats))
    # merge neighbours that are really the same view
    out = [bounds[0]]
    for k in range(1, len(bounds) - 1):
        a = _spread(out[-1], bounds[k])
        b = _spread(bounds[k], bounds[k + 1])
        if _exceeds(median_motion([feats[i] for i in a], [feats[j] for j in b]), MOVE):
            out.append(bounds[k])
    out.append(bounds[-1])
    return out


def _spread(i0, i1, k=N_SAMPLES):
    return np.unique(np.linspace(i0, i1 - 1, k).round().astype(int))


class Library:
    """One camera's known views: id -> sample frames (and their features)."""

    def __init__(self, root, camera):
        self.dir = Path(root) / str(camera)
        self.views = {}
        for f in sorted(self.dir.glob("v*.npz"), key=lambda p: int(p.stem[1:])):
            with np.load(f) as z:
                frames = z["frames"]
            self.views[f.stem] = (frames, [features(im) for im in frames])

    def match(self, feats):
        """(view_id, motion) of the closest stored view within SAME, or (None, None)."""
        best = (None, None, np.inf)
        for vid, (_, vfeats) in self.views.items():
            p = median_motion(feats, vfeats)
            if p is not None and not _exceeds(p, SAME):
                score = np.hypot(p[0], p[1]) + 100 * abs(p[2] - 1) + abs(p[3])
                if score < best[2]:
                    best = (vid, p, score)
        return best[0], best[1]

    def add(self, frames):
        vid = f"v{len(self.views) + 1}"
        while (self.dir / f"{vid}.npz").exists():   # never reuse an id
            vid = f"v{int(vid[1:]) + 1}"
        self.dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.dir / f"{vid}.npz", frames=frames)
        cv2.imwrite(str(self.dir / f"{vid}.png"), np.hstack(list(frames)))
        self.views[vid] = (frames, [features(im) for im in frames])
        return vid


def process(video_path, camera, library, sync_offset_s=None):
    """Split one video into steady stretches and name each one's view.

    Returns (result, new_views): `result` is what goes in the per-video JSON;
    `new_views` lists the view ids this video added to the library.
    """
    times, frames = keyframes(video_path)
    if len(times) == 0:
        raise ValueError(f"no keyframes decoded from {video_path}")
    feats = [features(im) for im in frames]
    bounds = segment(feats)
    period = float(np.median(np.diff(times))) if len(times) > 1 else 1.0

    stretches, new_views, prev = [], [], None
    for i0, i1 in zip(bounds[:-1], bounds[1:]):
        t0, t1 = float(times[i0]), float(times[i1 - 1]) + period
        idx = _spread(i0, i1)
        s = {"t0": round(t0, 3), "t1": round(t1, 3), "dur_s": round(t1 - t0, 1),
             "ref_t0": None if sync_offset_s is None else round(t0 + sync_offset_s, 3),
             "ref_t1": None if sync_offset_s is None else round(t1 + sync_offset_s, 3),
             "view": None, "match": None, "move_from_prev": None}
        sf = [feats[i] for i in idx]
        if prev is not None:
            p = median_motion(prev, sf)
            s["move_from_prev"] = None if p is None else [round(x, 3) for x in p]
        if t1 - t0 >= MIN_VIEW_S:
            vid, p = library.match(sf)
            if vid is None:
                vid = library.add(frames[idx])
                new_views.append(vid)
            else:
                s["match"] = [round(x, 3) for x in p]
            s["view"] = vid
        stretches.append(s)
        prev = sf

    result = {"video": Path(video_path).name, "camera": str(camera),
              "sync_offset_s": sync_offset_s, "keyframes": len(times),
              "keyframe_period_s": round(period, 4), "params": PARAMS,
              "stretches": stretches}
    return result, new_views


def save(result, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1))
    return out_path


def usage(result):
    """{view_id: (seconds, n_stretches)} for one processed video."""
    u = {}
    for s in result["stretches"]:
        if s["view"]:
            sec, n = u.get(s["view"], (0.0, 0))
            u[s["view"]] = (sec + s["dur_s"], n + 1)
    return u
