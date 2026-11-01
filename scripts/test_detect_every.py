#!/usr/bin/env python
"""Test: does running the person detector only every Nth frame change 2D pose?

    python scripts/test_detect_every.py            # md-pose env, GPU, ~1.5 h
    python scripts/test_detect_every.py --clip-s 4 --clips 2 --out-dir /tmp/x   # smoke test

`--detect-every N` reuses the detector's boxes for the N-1 frames in between;
the pose network still runs every frame. Detection is ~80% of pose time, so
N = 2 or 3 could cut pose cost a lot -- if accuracy holds. This measures it.

Design (fixed 2026-11-01, before any results):
  - Clips: one per room that has finished pose, up to CLIPS_MAX, each
    CLIP_S long, from the video with the most people, at the window where
    people move most (largest frame-to-frame keypoint motion). Hardest case
    for reused boxes, which lag a fast mover.
  - Each clip runs with N = 1, 2, 3 in this one process, one after another,
    so timings compare fairly. N = 1 is the reference.
  - Per frame, people in the reference and the test run are paired greedily
    by median distance over keypoints confident (> KPT_THR) in both; a pair
    counts if that distance is under MATCH_PX. A "person" is a slot with at
    least MIN_KPTS confident keypoints.
  - PASS for a given N needs all of:
      median keypoint shift  <= MEDIAN_PX_MAX
      95th percentile shift  <= P95_PX_MAX
      missed people          <= MISS_MAX of reference person-frames
      extra people           <= EXTRA_MAX of reference person-frames
    For scale: the same video run on a Mac (CoreML) and on tofino (CUDA)
    differed by a median of 1.0 px and a 95th percentile of 4.1 px on
    confident keypoints (260656/23, 2026-10-26): backend numerics alone.

Writes docs/detect_every_test.md (the report) and
logs/detect_every_test.json (all numbers).
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from multidata import pose  # noqa: E402

CLIP_S = 300
CLIPS_MAX = 15
NS = (1, 2, 3)
KPT_THR = 0.3
MIN_KPTS = 5
MATCH_PX = 30.0
MEDIAN_PX_MAX = 2.0
P95_PX_MAX = 8.0
MISS_MAX = 0.01
EXTRA_MAX = 0.01


def people(kp, sc):
    """[(slot, keypoints, confident mask)] for one frame."""
    out = []
    for s in range(kp.shape[0]):
        m = sc[s] > KPT_THR
        if m.sum() >= MIN_KPTS:
            out.append((s, kp[s], m))
    return out


def compare(ref, test):
    """Pair people frame by frame; collect keypoint shifts and misses."""
    shifts, n_ref, missed, extra = [], 0, 0, 0
    T = min(ref["keypoint"].shape[1], test["keypoint"].shape[1])
    for t in range(T):
        a = people(ref["keypoint"][:, t], ref["keypoint_score"][:, t])
        b = people(test["keypoint"][:, t], test["keypoint_score"][:, t])
        n_ref += len(a)
        cand = []
        for i, (_, ka, ma) in enumerate(a):
            for j, (_, kb, mb) in enumerate(b):
                both = ma & mb
                if both.sum() >= 3:
                    d = np.linalg.norm(ka[both] - kb[both], axis=1)
                    cand.append((float(np.median(d)), i, j, d))
        cand.sort(key=lambda c: c[0])
        used_a, used_b = set(), set()
        for med, i, j, d in cand:
            if med > MATCH_PX or i in used_a or j in used_b:
                continue
            used_a.add(i)
            used_b.add(j)
            shifts.append(d)
        missed += len(a) - len(used_a)
        extra += len(b) - len(used_b)
    d = np.concatenate(shifts) if shifts else np.array([np.nan])
    return {"frames": T, "ref_person_frames": n_ref,
            "median_px": float(np.median(d)), "p95_px": float(np.percentile(d, 95)),
            "missed": missed, "extra": extra,
            "missed_frac": missed / max(n_ref, 1), "extra_frac": extra / max(n_ref, 1)}


def pick_clips():
    """One clip per room: the busiest video, at its most active window."""
    c = sqlite3.connect(f"file:{ROOT / 'manifest.sqlite'}?mode=ro", uri=True)
    rows = c.execute(
        "SELECT v.case_id, v.camera, v.filepath, cs.room_number FROM videos v "
        "JOIN cases cs USING (case_id) WHERE v.pose_status = 'done' "
        "ORDER BY CAST(cs.room_number AS INT), v.case_id, v.camera").fetchall()
    best = {}
    for case_id, cam, path, room in rows:
        e = pose.load(ROOT / "data" / "pose" / case_id / f"{cam}.npz")
        sc, kp = e["keypoint_score"], e["keypoint"]
        n = (sc > KPT_THR).sum(axis=2) >= MIN_KPTS          # (M, T) people
        busy = n.sum(axis=0)
        if busy.mean() > best.get(room, (None, -1))[1]:
            best[room] = ((case_id, cam, path, kp, sc, n), busy.mean())
    clips = []
    for room, ((case_id, cam, path, kp, sc, n), _) in sorted(best.items(), key=lambda x: int(x[0])):
        T, fps = kp.shape[1], 30
        win = min(CLIP_S * fps, T)
        # motion per frame: mean keypoint displacement of present people
        disp = np.linalg.norm(np.diff(kp, axis=1), axis=3).mean(axis=2)   # (M, T-1)
        disp = np.where(n[:, 1:] & n[:, :-1], disp, 0).sum(axis=0)
        disp = np.clip(disp, 0, 50)        # a slot switching people isn't motion
        score = np.convolve(disp, np.ones(win), "valid") if T > win else np.array([0.0])
        start = int(np.argmax(score))
        clips.append({"room": room, "case_id": case_id, "camera": cam, "path": path,
                      "start_frame": start, "frames": win})
    return clips[:CLIPS_MAX]


def main():
    global CLIP_S, CLIPS_MAX
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clip-s", type=int, default=CLIP_S)
    ap.add_argument("--clips", type=int, default=CLIPS_MAX)
    ap.add_argument("--out-dir", default=None,
                    help="write report + json here instead of docs/ and logs/")
    args = ap.parse_args()
    CLIP_S, CLIPS_MAX = args.clip_s, args.clips
    started = datetime.now().isoformat(timespec="seconds")
    clips = pick_clips()
    results = []
    for clip in clips:
        runs = {}
        for N in NS:
            t0 = time.perf_counter()
            entry, details = pose.extract(ROOT / clip["path"], device="cuda", detect_every=N,
                                          start_frame=clip["start_frame"],
                                          max_frames=clip["frames"])
            runs[N] = (entry, details, time.perf_counter() - t0)
        res = {**clip, "timing": {N: {"wall_s": round(w, 1), "fps": d["fps_processed"],
                                      "detect_s": d["detect_s"], "pose_s": d["pose_s"]}
                                  for N, (_, d, w) in runs.items()},
               "vs_every_frame": {N: compare(runs[1][0], runs[N][0]) for N in NS if N != 1}}
        results.append(res)
        print(f"room {clip['room']} {clip['case_id']}/{clip['camera']}: "
              + "  ".join(f"N={N} {v['median_px']:.2f}/{v['p95_px']:.1f}px "
                          f"miss {100 * v['missed_frac']:.2f}%"
                          for N, v in res["vs_every_frame"].items()), flush=True)

    summary = {}
    for N in NS[1:]:
        shifts_med = [r["vs_every_frame"][N]["median_px"] for r in results]
        ref = sum(r["vs_every_frame"][N]["ref_person_frames"] for r in results)
        missed = sum(r["vs_every_frame"][N]["missed"] for r in results)
        extra = sum(r["vs_every_frame"][N]["extra"] for r in results)
        p95 = max(r["vs_every_frame"][N]["p95_px"] for r in results)
        med = float(np.median(shifts_med))
        speed = (sum(r["timing"][N]["fps"] * r["frames"] for r in results)
                 / sum(r["timing"][1]["fps"] * r["frames"] for r in results))
        s = {"median_px": med, "worst_clip_p95_px": p95,
             "missed_frac": missed / max(ref, 1), "extra_frac": extra / max(ref, 1),
             "speedup_vs_every_frame": speed}
        s["pass"] = bool(med <= MEDIAN_PX_MAX and p95 <= P95_PX_MAX
                         and s["missed_frac"] <= MISS_MAX and s["extra_frac"] <= EXTRA_MAX)
        summary[N] = s
    passing = [N for N in NS[1:] if summary[N]["pass"]]
    verdict = max(passing) if passing else 1

    out = {"started": started, "finished": datetime.now().isoformat(timespec="seconds"),
           "thresholds": {"median_px": MEDIAN_PX_MAX, "p95_px": P95_PX_MAX,
                          "missed": MISS_MAX, "extra": EXTRA_MAX, "match_px": MATCH_PX,
                          "kpt_thr": KPT_THR, "min_kpts": MIN_KPTS},
           "summary": summary, "verdict_detect_every": verdict, "clips": results}
    json_path = (Path(args.out_dir) if args.out_dir else ROOT / "logs") / "detect_every_test.json"
    md_path = (Path(args.out_dir) if args.out_dir else ROOT / "docs") / "detect_every_test.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(out, indent=1, default=str))
    write_report(out, md_path)
    print(f"verdict: detect_every = {verdict}")


def write_report(out, md_path):
    s, th = out["summary"], out["thresholds"]
    lines = [
        "# Test: running the person detector every Nth frame",
        "",
        f"Run {out['started']} to {out['finished']} on tofino by "
        "`scripts/test_detect_every.py`; all numbers in `logs/detect_every_test.json`.",
        "",
        f"**Verdict: `--detect-every {out['verdict_detect_every']}`**"
        + (" (no stride passed; keep every frame)." if out["verdict_detect_every"] == 1
           else " is the largest stride that passed every threshold."),
        "",
        "Thresholds were fixed before the run: median keypoint shift "
        f"<= {th['median_px']} px, 95th percentile <= {th['p95_px']} px (worst clip), "
        f"missed and extra people <= {100 * th['missed']:.0f}% of person-frames, "
        "all measured against running the detector on every frame.",
        "",
        "| Detector runs | Median shift (px) | Worst clip 95th pct (px) | Missed people | "
        "Extra people | Speed vs every frame | Pass |",
        "|---|---|---|---|---|---|---|",
    ]
    for N, v in s.items():
        lines.append(f"| every {N} frames | {v['median_px']:.2f} | {v['worst_clip_p95_px']:.1f} "
                     f"| {100 * v['missed_frac']:.2f}% | {100 * v['extra_frac']:.2f}% "
                     f"| {v['speedup_vs_every_frame']:.2f}x | {'yes' if v['pass'] else 'no'} |")
    lines += ["", "## Clips", "",
              f"One per room: the video with the most people, at its {CLIP_S} s of most "
              "movement (the hardest case for reused detector boxes).", "",
              "| Room | Case/camera | Start (s) | Every 2: median / p95 px, missed | "
              "Every 3: median / p95 px, missed |",
              "|---|---|---|---|---|"]
    for r in out["clips"]:
        cells = []
        for N in (2, 3):
            v = r["vs_every_frame"].get(N) or r["vs_every_frame"].get(str(N))
            cells.append(f"{v['median_px']:.2f} / {v['p95_px']:.1f}, "
                         f"{100 * v['missed_frac']:.2f}%")
        lines.append(f"| {r['room']} | {r['case_id']}/{r['camera']} | "
                     f"{r['start_frame'] / 30:.0f} | {cells[0]} | {cells[1]} |")
    lines += ["", "Caveat: this measures change from the every-frame run, not error "
              "against hand-marked truth. A passing stride gives the same answer as "
              "every frame within these tolerances; it doesn't say either is right.", ""]
    md_path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
