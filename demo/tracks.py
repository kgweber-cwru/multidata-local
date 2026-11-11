"""Link per-frame pose detections into tracklets (one person, unbroken).

The pose files have no tracking: slot 0 in one frame and the next can be
different people. This joins a detection to the open tracklet whose last box
overlaps it most, allowing short gaps. Tracklets break at camera moves and
when people cross; that's fine for the demo, where tracklets get a role by
hand (labels/<case>.json) or stay "person".
"""
import numpy as np

KPT_THR = 0.3
MIN_KPTS = 5          # a detection needs this many confident keypoints
MIN_IOU = 0.3
MAX_GAP = 15          # frames a tracklet may go undetected and still continue


def iou(a, b):
    x0, y0 = np.maximum(a[:2], b[:2])
    x1, y1 = np.minimum(a[2:], b[2:])
    inter = max(x1 - x0, 0) * max(y1 - y0, 0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def track(entry):
    """(M, T) int array: tracklet id per slot per frame, -1 for no/weak detection."""
    sc, bbox = entry["keypoint_score"], entry["bbox"]
    M, T = sc.shape[:2]
    valid = (sc > KPT_THR).sum(axis=2) >= MIN_KPTS
    ids = -np.ones((M, T), int)
    open_ = {}                        # id -> (last frame, last box)
    nxt = 0
    for t in range(T):
        slots = np.flatnonzero(valid[:, t])
        cands = sorted(((iou(bbox[s, t], box), s, k) for s in slots
                        for k, (lt, box) in open_.items() if t - lt <= MAX_GAP),
                       reverse=True)
        used_s, used_k = set(), set()
        for v, s, k in cands:
            if v < MIN_IOU:
                break
            if s in used_s or k in used_k:
                continue
            ids[s, t] = k
            used_s.add(s)
            used_k.add(k)
        for s in slots:
            if s not in used_s:
                ids[s, t] = nxt
                nxt += 1
        for s in slots:
            open_[ids[s, t]] = (t, bbox[s, t])
        open_ = {k: v for k, v in open_.items() if t - v[0] <= MAX_GAP}
    return ids


def summary(ids):
    """{id: (n_frames, first, last)}"""
    out = {}
    M, T = ids.shape
    for s in range(M):
        for t in np.flatnonzero(ids[s] >= 0):
            k = ids[s, t]
            n, a, b = out.get(k, (0, t, t))
            out[k] = (n + 1, min(a, t), max(b, t))
    return out
