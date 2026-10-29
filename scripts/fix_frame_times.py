#!/usr/bin/env python
"""Make frame times line up with pose frames where OpenCV skipped frames.

    python scripts/fix_frame_times.py            # md-pose env; dry run
    python scripts/fix_frame_times.py --write

The sync stage (`run_stage.py sync`, md-speech) stores every frame's timestamp
from the file (ffprobe). Pose reads video with OpenCV, which on some files
returns fewer frames -- so pose frame i is not file frame i, and
`sync.reference_times(..., n_frames=T)` refuses to pair them. This decodes
each such file with OpenCV (no pose network; ~5 s per 20 min of video),
matches each decoded frame to its real timestamp (`sync.snap_to_pts`), and
rewrites `data/sync/<case_id>/<camera>.npz` so entry i is pose frame i.

Only touches files whose pose frame count differs from their stored frame
times. Re-running the sync stage on a row overwrites the fix, so run this
again after it.
"""
import argparse
import logging
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest, sync  # noqa: E402
from multidata.paths import DATA_DIR, MANIFEST_PATH  # noqa: E402

log = logging.getLogger("fix_frame_times")


def decoder_times(video_path):
    cap = cv2.VideoCapture(str(video_path))
    times = []
    while cap.grab():
        times.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000)
    cap.release()
    return np.array(times)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="rewrite the frame-time files")
    ap.add_argument("--manifest", default=MANIFEST_PATH)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    fixed = 0
    for v in manifest.all_videos(args.manifest):
        pose_path = DATA_DIR / "pose" / v.case_id / f"{v.camera}.npz"
        times_path = DATA_DIR / "sync" / v.case_id / f"{v.camera}.npz"
        if not (pose_path.exists() and times_path.exists()):
            continue
        with np.load(pose_path) as z:
            T = z["keypoint"].shape[1]
        pts = sync.load_frame_times(times_path)
        if T == len(pts):
            continue
        dec = decoder_times(ROOT / v.filepath)
        if len(dec) != T:
            log.error("%s/%s: OpenCV now returns %d frames but pose has %d -- "
                      "can't tell which frames pose saw; leaving it", v.case_id, v.camera, len(dec), T)
            continue
        snapped = sync.snap_to_pts(dec, pts)
        err = np.abs(snapped - dec)
        log.info("%s/%s: pose %d frames, file %d; snapped (max |decoder - file| %.1f ms)%s",
                 v.case_id, v.camera, T, len(pts), 1000 * err.max(),
                 "" if args.write else "  [dry run]")
        if args.write:
            sync.save_frame_times(snapped, times_path)
        fixed += 1
    log.info("%d file(s) %s", fixed, "fixed" if args.write else "would be fixed")


if __name__ == "__main__":
    main()
