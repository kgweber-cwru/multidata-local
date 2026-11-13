#!/usr/bin/env python
"""Contact sheets of a case's longest tracklets, for writing labels/<case>.json.

    python demo/tracklet_sheets.py 261456            # every camera
    python demo/tracklet_sheets.py 261456 --camera 31 --top 40

md-pose env. Writes data/demo/sheets/<case>_<cam>_<n>.jpg: one row per
tracklet (id, time span, frames) with three crops from its start, middle and
end. Look at them and name the ids in demo/labels/<case>.json; reflections
and objects get "ignore".
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "demo"))
from multidata import pose  # noqa: E402
import tracks  # noqa: E402

ROW_H, CROP_W, PER_SHEET = 160, 200, 14


def crop_row(cap, e, ids, k, n, a, b):
    t = e["frame_ms"] / 1000
    row = []
    for f in np.linspace(a, b, 3).astype(int):
        frames = np.flatnonzero((ids == k).any(0))
        f = frames[np.argmin(abs(frames - f))]
        s = np.flatnonzero(ids[:, f] == k)[0]
        x0, y0, x1, y1 = e["bbox"][s, f].astype(int)
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, fr = cap.read()
        crop = fr[max(y0, 0):max(y1, 1), max(x0, 0):max(x1, 1)] if ok else np.zeros((1, 1, 3), np.uint8)
        if crop.size == 0:
            crop = np.zeros((ROW_H, 80, 3), np.uint8)
        w = max(int(ROW_H * crop.shape[1] / crop.shape[0]), 1)
        crop = cv2.resize(crop, (w, ROW_H))[:, :CROP_W]
        row.append(cv2.copyMakeBorder(crop, 0, 0, 0, CROP_W - crop.shape[1], cv2.BORDER_CONSTANT))
    lab = np.zeros((ROW_H, 150, 3), np.uint8)
    cv2.putText(lab, str(k), (5, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    cv2.putText(lab, f"{t[a] / 60:.1f}-{t[b] / 60:.1f} min", (5, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(lab, f"n={n}", (5, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return np.hstack([lab] + row)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case")
    ap.add_argument("--camera", action="append")
    ap.add_argument("--top", type=int, default=42)
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{ROOT / 'manifest.sqlite'}?mode=ro", uri=True)
    vids = db.execute("SELECT camera, filepath FROM videos WHERE recording_id=?", (args.case,)).fetchall()
    out = ROOT / "data" / "demo" / "sheets"
    out.mkdir(parents=True, exist_ok=True)
    for cam, path in vids:
        if args.camera and cam not in args.camera:
            continue
        e = pose.load(ROOT / "data" / "pose" / args.case / f"{cam}.npz")
        ids = tracks.track(e)
        summ = tracks.summary(ids)
        best = sorted(summ, key=lambda k: -summ[k][0])[:args.top]
        cap = cv2.VideoCapture(str(ROOT / path))
        rows = [crop_row(cap, e, ids, k, *summ[k]) for k in best]
        for i in range(0, len(rows), PER_SHEET):
            p = out / f"{args.case}_{cam}_{i // PER_SHEET}.jpg"
            cv2.imwrite(str(p), np.vstack(rows[i:i + PER_SHEET]), [cv2.IMWRITE_JPEG_QUALITY, 70])
            print(p)


if __name__ == "__main__":
    main()
