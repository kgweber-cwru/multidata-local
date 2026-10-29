#!/usr/bin/env python
"""Utility: render a short side-by-side pose overlay of a case's two cameras,
with the same person drawn in the same color in both views.

    python scripts/render_two_view_overlay.py --case 255387 --start 145 --dur 45 \
        --out data/pose_overlays/demo/255387_two_view_2m25-3m10.mp4     # md-pose env

For each camera and each frame, every detection is labelled:
  mirror   - at least MIRROR_FRAC of its confident body keypoints fall inside
             that camera's hand-drawn mirror outline (MIRRORS below)
  patient  - of the rest, the one nearest the patient anchor
  learner  - the best-scoring detection after that
  other    - anything left over
The patient anchor is the densest spot of non-mirror detections in the clip:
the patient sits still and the learner moves around.

Limits (see docs/pose_parked.md, "Mirrors" and "Cameras move"):
  - Identity is decided per frame by position. There's no tracker.
  - The patient anchor needs a clip long enough for the learner to move
    around (tens of seconds). On a 3-second clip of 255387 it found the
    wrong person in both cameras.
  - Mirror outlines are drawn by hand for each camera and only hold while the
    camera doesn't move. Camera 15 pans and tilts, so pick a clip where it
    holds still.
  - Frames are paired by time on the case's reference clock, from the sync
    stage (`run_stage.py sync`: frame timestamps + audio offset; see
    multidata.sync). Without sync data for a camera it falls back to frame
    number / fps with no offset, and warns: the views can then be up to ~1 s
    apart, more where a file dropped frames.

Writes an mp4v file; for browser playback, re-encode to H.264 with ffmpeg
(md-speech has it): ffmpeg -i in.mp4 -c:v libx264 -pix_fmt yuv420p out.mp4

Not a manifest-driven stage: a one-off inspection tool, like
sample_pose_overlays.py.
"""
import argparse
import logging
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest, pose, sync  # noqa: E402
from multidata.paths import DATA_DIR, MANIFEST_PATH, ROOT as REPO  # noqa: E402

log = logging.getLogger("render_two_view_overlay")

# Mirror outlines in full-resolution (1280x720) pixels, per camera. Each one
# holds only for the camera's view at the time it was drawn.
MIRRORS = {
    "327": [(250, 0), (690, 0), (690, 150), (265, 220)],  # drawn on case 255387
}
KPT_THR = 0.3
MIRROR_FRAC = 0.8
COLORS = {"patient": (60, 170, 255), "learner": (255, 160, 40),  # BGR
          "mirror": (200, 80, 200), "other": (120, 120, 120)}

# COCO-WholeBody links: body + feet, then both hands (keypoints 91-111, 112-132).
BODY = [(15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12), (5, 6),
        (5, 7), (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2), (1, 3), (2, 4),
        (15, 17), (15, 18), (15, 19), (16, 20), (16, 21), (16, 22)]
HAND = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (0, 9), (9, 10),
        (10, 11), (11, 12), (0, 13), (13, 14), (14, 15), (15, 16), (0, 17), (17, 18),
        (18, 19), (19, 20)]
LINKS = BODY + [(a + 91, b + 91) for a, b in HAND] + [(a + 112, b + 112) for a, b in HAND]


def _in_mirror(mirror, x, y):
    return mirror is not None and cv2.pointPolygonTest(mirror, (float(x), float(y)), False) >= 0


def label_frame(kp, sc, mirror, anchor):
    """kp (M, V, 2), sc (M, V) for one frame -> {slot: label}."""
    present, labels = [], {}
    for s in range(kp.shape[0]):
        vis = sc[s, :17] > KPT_THR
        if vis.sum() < 4:
            continue
        pts = kp[s, :17][vis]
        if mirror is not None and np.mean([_in_mirror(mirror, x, y) for x, y in pts]) >= MIRROR_FRAC:
            labels[s] = "mirror"
            continue
        present.append((s, pts.mean(0), sc[s, :17][vis].mean()))
    if present:
        p = min(present, key=lambda r: np.linalg.norm(r[1] - anchor))
        labels[p[0]] = "patient"
        rest = sorted((r for r in present if r[0] != p[0]), key=lambda r: -r[2])
        for i, r in enumerate(rest):
            labels[r[0]] = "learner" if i == 0 else "other"
    return labels


def patient_anchor(kp, sc, mirror):
    """Densest 40-px cell of non-mirror detection centers over the clip."""
    pts = []
    for t in range(0, kp.shape[1], 5):
        for s in range(kp.shape[0]):
            vis = sc[s, t, :17] > KPT_THR
            if vis.sum() >= 4:
                c = kp[s, t, :17][vis].mean(0)
                if not _in_mirror(mirror, *c):
                    pts.append(c)
    if not pts:
        return np.array([640.0, 360.0])
    pts = np.array(pts)
    H, xe, ye = np.histogram2d(pts[:, 0], pts[:, 1],
                               bins=[np.arange(0, 1321, 40), np.arange(0, 761, 40)])
    i, j = np.unravel_index(H.argmax(), H.shape)
    return np.array([xe[i] + 20, ye[j] + 20])


def draw(frame, kp, sc, labels):
    # mirror skeletons first, so real people draw on top
    for s, lab in sorted(labels.items(), key=lambda kv: kv[1] != "mirror"):
        col, k, c = COLORS[lab], kp[s], sc[s]
        thick = 1 if lab == "other" else 2
        for a, b in LINKS:
            if c[a] > KPT_THR and c[b] > KPT_THR:
                cv2.line(frame, tuple(map(int, k[a])), tuple(map(int, k[b])), col, thick, cv2.LINE_AA)
        for v in range(133):
            if c[v] > KPT_THR:
                cv2.circle(frame, tuple(map(int, k[v])), 1 if v >= 23 else 3, col, -1, cv2.LINE_AA)
        vis = c[:17] > KPT_THR
        if vis.any():
            x, y = k[:17][vis, 0].min(), k[:17][vis, 1].min()
            cv2.putText(frame, lab, (int(x), max(int(y) - 8, 14)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, col, 2, cv2.LINE_AA)
    return frame


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--case", required=True)
    ap.add_argument("--cameras", nargs=2, help="two camera ids (default: the case's two)")
    ap.add_argument("--start", type=float, required=True,
                    help="clip start, seconds on the reference camera's clock")
    ap.add_argument("--dur", type=float, default=45, help="clip length, seconds")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default=MANIFEST_PATH)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    videos = {v.camera: v for v in manifest.all_videos(args.manifest) if v.case_id == args.case}
    cams = args.cameras or sorted(videos)
    if len(cams) != 2 or any(c not in videos for c in cams):
        sys.exit(f"case {args.case}: need exactly two cameras, have {sorted(videos)}")

    cfg = {}
    for cam in cams:
        v = videos[cam]
        cap = cv2.VideoCapture(str(REPO / v.filepath))
        e = pose.load(DATA_DIR / "pose" / args.case / f"{cam}.npz")
        T = e["keypoint"].shape[1]
        times_path = DATA_DIR / "sync" / args.case / f"{cam}.npz"
        if times_path.exists() and v.sync_offset_s is not None:
            ref_t = sync.reference_times(sync.load_frame_times(times_path), v.sync_offset_s,
                                         n_frames=T)
        else:
            log.warning("camera %s: no sync data -- pairing by frame number, unsynced", cam)
            ref_t = np.arange(T) / cap.get(cv2.CAP_PROP_FPS)
        mirror = np.array(MIRRORS[cam], np.int32) if cam in MIRRORS else None
        cfg[cam] = dict(cap=cap, fps=cap.get(cv2.CAP_PROP_FPS), kp=e["keypoint"],
                        sc=e["keypoint_score"], ref_t=ref_t, mirror=mirror)

    fps = cfg[cams[0]]["fps"]
    n = int(args.dur * fps)
    ticks = args.start + np.arange(n) / fps  # output frame times, reference clock
    for cam, c in cfg.items():
        # each output frame shows each camera's frame nearest that reference time
        c["idx"], _ = sync.nearest_frames(ticks, c["ref_t"])
        lo, hi = c["idx"][0], c["idx"][-1] + 1
        c["anchor"] = patient_anchor(c["kp"][:, lo:hi], c["sc"][:, lo:hi], c["mirror"])
        c["cap"].set(cv2.CAP_PROP_POS_FRAMES, lo)
        c["pos"], c["frame"] = lo - 1, None
        log.info("camera %s: %.3f fps, frames %d-%d, patient anchor %s",
                 cam, c["fps"], lo, hi - 1, c["anchor"])

    W, H = 960, 540
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(args.out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (2 * W, H + 40))
    for i in range(n):
        panels = []
        for cam in cams:
            c = cfg[cam]
            t = int(c["idx"][i])
            while c["pos"] < t:
                ok, frame = c["cap"].read()
                c["pos"] += 1
                c["frame"] = frame if ok else np.zeros((720, 1280, 3), np.uint8)
            frame = c["frame"].copy()
            if t < c["kp"].shape[1]:
                kp, sc = c["kp"][:, t], c["sc"][:, t]
                labels = label_frame(kp, sc, c["mirror"], c["anchor"])
                if c["mirror"] is not None:
                    cv2.polylines(frame, [c["mirror"]], True, (150, 150, 150), 1, cv2.LINE_AA)
                frame = draw(frame, kp, sc, labels)
            frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
            cv2.putText(frame, f"camera {cam}", (12, H - 14), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (255, 255, 255), 2, cv2.LINE_AA)
            panels.append(frame)
        bar = np.full((40, 2 * W, 3), 30, np.uint8)
        ts = args.start + i / fps
        cv2.putText(bar, f"case {args.case}   t = {int(ts // 60)}:{ts % 60:05.2f}", (12, 27),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (230, 230, 230), 1, cv2.LINE_AA)
        x = 900
        for lab in ("patient", "learner", "mirror"):
            cv2.circle(bar, (x, 20), 7, COLORS[lab], -1)
            name = "mirror reflection (ignored)" if lab == "mirror" else lab
            cv2.putText(bar, name, (x + 14, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        COLORS[lab], 1, cv2.LINE_AA)
            x += 160
        writer.write(np.vstack([np.hstack(panels), bar]))
    writer.release()
    log.info("wrote %s (%d frames)", args.out, n)


if __name__ == "__main__":
    main()
