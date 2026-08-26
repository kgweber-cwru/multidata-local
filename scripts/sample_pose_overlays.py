#!/usr/bin/env python
"""Utility: paint pose overlays onto one short/medium/long video, picked at
random, for a quick eyeball inspection of raw pose quality (jitter, slot
stability, `--detect-every` artifacts) before deciding on any smoothing pass.

    python scripts/sample_pose_overlays.py            # md-pose env

Candidates are video rows whose `pose_status` is `done` *and* whose
`.pkl` is actually on disk (manifest status can drift from disk state --
a row re-marked pending, a pkl deleted by hand). Candidates are sorted by
`duration_s` and split into three equal-ish terciles (short/medium/long,
not fixed-duration cutoffs -- the real duration distribution isn't known up
front); one video is picked at random from each. `pose.render_overlay()`
draws directly from the already-extracted keypoints -- no detector/pose
network re-run.

Output: `data/pose_overlays/sample/<bucket>_<case_id>_<camera>.mp4`.

Not a manifest-driven stage (no status column, nothing resumable) -- this is
a one-off inspection tool, unlike run_stage.py.
"""
import argparse
import logging
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest  # noqa: E402
from multidata.paths import DATA_DIR  # noqa: E402

log = logging.getLogger("sample_pose_overlays")

BUCKETS = ("short", "medium", "long")


def _candidates(manifest_path):
    """(Video, pkl_path) pairs for rows with a done pose_status and a real
    pose pkl on disk."""
    candidates = []
    for v in manifest.all_videos(manifest_path):
        if v.pose_status != "done" or not v.duration_s:
            continue
        pkl = DATA_DIR / "pose" / v.case_id / f"{v.camera}.pkl"
        if pkl.exists():
            candidates.append((v, pkl))
    return candidates


def _bucket(candidates):
    """Duration-sorted candidates split into 3 equal-ish terciles; one random
    pick per non-empty tercile."""
    candidates = sorted(candidates, key=lambda pair: pair[0].duration_s)
    n = len(candidates)
    edges = [0, n // 3, 2 * n // 3, n]
    picks = {}
    for name, lo, hi in zip(BUCKETS, edges, edges[1:]):
        chunk = candidates[lo:hi]
        if chunk:
            picks[name] = random.choice(chunk)
    return picks


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(ROOT / "manifest.sqlite"))
    ap.add_argument("--out-dir", default=str(DATA_DIR / "pose_overlays" / "sample"))
    ap.add_argument("--seed", type=int, default=None,
                    help="fix the random picks for a reproducible sample "
                         "(default: different videos every run)")
    ap.add_argument("--kpt-thr", type=float, default=None,
                    help="override pose.KPT_THR, the overlay draw/visibility threshold")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    if args.seed is not None:
        random.seed(args.seed)

    candidates = _candidates(args.manifest)
    if not candidates:
        log.error("no videos with pose_status=done and an on-disk .pkl found")
        return 1

    picks = _bucket(candidates)
    missing = [b for b in BUCKETS if b not in picks]
    if missing:
        log.warning("only %d candidate video(s) total -- no pick for: %s",
                   len(candidates), ", ".join(missing))

    import mmengine  # md-pose env only; deferred so --help works without the env active

    from multidata import pose

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    render_kwargs = {"kpt_thr": args.kpt_thr} if args.kpt_thr is not None else {}

    for bucket, (video, pkl) in picks.items():
        video_path = ROOT / video.filepath
        out_path = out_dir / f"{bucket}_{video.case_id}_{video.camera}.mp4"
        log.info("%-6s %5.0fs  %s/%s  ->  %s",
                 bucket, video.duration_s, video.case_id, video.camera, out_path)
        entry = mmengine.load(str(pkl))
        pose.render_overlay(video_path, entry, out_path, **render_kwargs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
