#!/usr/bin/env python
"""Utility: convert float32 pose `.pkl` outputs to float16 compressed `.npz`.

    python scripts/convert_pose.py            # md-pose env; dry run, sizes only
    python scripts/convert_pose.py --write    # write .npz next to each .pkl
    python scripts/convert_pose.py --write --delete-pkl

No re-run of the pose network: the existing keypoints are re-saved through
`pose.save()`, the same writer the pose stage now uses. Each `.npz` is read
back and checked against its `.pkl` (same metadata and shapes, coordinates
within 0.5 px -- float16's worst case on a 1280-wide frame -- and scores
within 0.001) before the `.pkl` is deleted, and only with --delete-pkl.
Already-converted cameras (an `.npz` exists) are skipped, so it is safe to
re-run after an interruption.
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import pose  # noqa: E402
from multidata.paths import DATA_DIR  # noqa: E402

log = logging.getLogger("convert_pose")

MAX_COORD_ERR = 0.5
MAX_SCORE_ERR = 1e-3


def _check(old, new):
    for key in ("frame_dir", "total_frames", "img_shape", "label"):
        if old[key] != new[key]:
            raise ValueError(f"{key}: {old[key]!r} != {new[key]!r}")
    for key, tol in (("keypoint", MAX_COORD_ERR), ("keypoint_score", MAX_SCORE_ERR)):
        if old[key].shape != new[key].shape:
            raise ValueError(f"{key} shape {old[key].shape} != {new[key].shape}")
        err = float(np.abs(old[key] - new[key]).max())
        if err > tol:
            raise ValueError(f"{key} max error {err:.4f} > {tol}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pose-dir", default=str(DATA_DIR / "pose"))
    ap.add_argument("--write", action="store_true", help="write the .npz files")
    ap.add_argument("--delete-pkl", action="store_true",
                    help="delete each .pkl once its .npz has been verified")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    # <case_id>/<camera>.pkl only -- loose experiment files in the top level
    # (e.g. *_motion_only.pkl) are not stage outputs and are left alone.
    pkls = sorted(Path(args.pose_dir).glob("*/*.pkl"))
    before = after = 0
    failed = []
    for pkl in pkls:
        npz = pkl.with_suffix(".npz")
        if npz.exists():
            log.info("skip %s (already converted)", npz.relative_to(ROOT))
            continue
        if not args.write:
            before += pkl.stat().st_size
            continue
        try:
            old = pose.load(pkl)
            pose.save(old, npz)
            _check(old, pose.load(npz))
        except Exception as e:
            log.error("FAILED %s: %s", pkl.relative_to(ROOT), e)
            npz.unlink(missing_ok=True)
            failed.append(pkl)
            continue
        before += pkl.stat().st_size
        after += npz.stat().st_size
        log.info("%s  %.0f MB -> %.0f MB", npz.relative_to(ROOT),
                 pkl.stat().st_size / 1e6, npz.stat().st_size / 1e6)
        if args.delete_pkl:
            pkl.unlink()

    if not args.write:
        log.info("dry run: %d .pkl, %.1f GB; expect roughly 14x smaller. Pass --write.",
                 len(pkls), before / 1e9)
    else:
        log.info("converted %.1f GB -> %.1f GB; %d failed", before / 1e9, after / 1e9, len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
