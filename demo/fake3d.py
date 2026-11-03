#!/usr/bin/env python
"""A first, uncalibrated 3D reconstruction: the two cameras of one case,
during a stretch where neither moves.

    python demo/fake3d.py 261498 --from 262 --to 519

md-pose env (OpenCV, NumPy). Writes data/demo/cases/<case>/threed.json for
the explorer's 3D view, and prints how well it fits.

No calibration session yet, so the cameras calibrate themselves from the
people in the room ("self-calibration"):

1. Pair the two cameras' frames by time (sync offsets from the manifest).
2. Every person seen in both cameras at the same instant gives up to 26
   matched points (their keypoints). Which person in camera A is which in
   camera B isn't known, so every pairing is tried; a robust fit
   (MAGSAC) keeps the geometry most pairings agree on, then each frame's
   pairing is chosen by that geometry.
3. Focal lengths from each camera's vanishing points (floor tiles, door
   frames: demo/vanishing.py). The people alone can't pin these down here:
   both cameras look at the same spot from similar heights. Lens distortion
   is ignored and the principal point is assumed central.
4. Relative camera pose from the essential matrix of the matched people;
   triangulate everyone.
5. Scale from typical adult body segments (shoulders, arms, legs). Up is a
   vanishing direction (vertical room lines) chosen so both cameras hang
   above the people; the floor sits at standing or seated people's feet.
   The room then stands upright in metres.
6. People get stable ids over time (nearest body centre), and a light
   smoothing. The steadiest person is drawn as "patient?", the others as
   "learner?": a guess, not identification.

What it isn't: calibrated. Expect the shape to be right and the size to be
roughly right (about +-10%, from the body-size assumption). On 261498 and
261320 all four cameras came out 2.6-2.9 m up, 3.2-4.3 m apart, with
points reprojecting within about 1 px: plausible, not verified. It's the
roadmap's 3D core in miniature, and the calibration session replaces
steps 3-5.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from multidata import pose  # noqa: E402
sys.path.insert(0, str(ROOT / "demo"))
import vanishing  # noqa: E402

KP_THR = 0.35         # keypoint confidence for a correspondence
MIN_SHARED = 6        # keypoints both cameras must see to pair two detections
PAIR_ERR_PX = 25.0    # median epipolar distance (px) for an accepted pairing; wrong pairings sit at 75+
OUT_HZ = 10
# typical adult segment lengths in metres, keypoint pairs (HALPE_26)
SEGMENTS = {(5, 6): 0.37, (5, 7): 0.29, (6, 8): 0.29, (7, 9): 0.25, (8, 10): 0.25,
            (11, 13): 0.42, (12, 14): 0.42, (13, 15): 0.40, (14, 16): 0.40}
FEET = [15, 16, 20, 21, 22, 23, 24, 25]


def load(case, cam, offset):
    e = pose.load(ROOT / "data" / "pose" / case / f"{cam}.npz")
    return {"kp": e["keypoint"], "sc": e["keypoint_score"], "t": e["frame_ms"] / 1000 + offset,
            "w": int(e["img_shape"][1]), "h": int(e["img_shape"][0])}


def valid(c, i):
    return [s for s in range(c["sc"].shape[0]) if (c["sc"][s, i] > KP_THR).sum() >= MIN_SHARED]


def frame_pairs(A, B, t0, t1):
    out = []
    for i in np.flatnonzero((A["t"] >= t0) & (A["t"] < t1)):
        j = int(np.argmin(np.abs(B["t"] - A["t"][i])))
        if abs(B["t"][j] - A["t"][i]) < 0.02:
            out.append((i, j))
    return out


def shared(A, B, i, j, sa, sb):
    m = (A["sc"][sa, i] > KP_THR) & (B["sc"][sb, j] > KP_THR)
    return np.flatnonzero(m)


def epi_err(F, p1, p2):
    """Symmetric distance (px) of points to each other's epipolar lines."""
    h1, h2 = np.c_[p1, np.ones(len(p1))], np.c_[p2, np.ones(len(p2))]
    l2, l1 = h1 @ F.T, h2 @ F
    d2 = np.abs((l2 * h2).sum(1)) / np.hypot(l2[:, 0], l2[:, 1])
    d1 = np.abs((l1 * h1).sum(1)) / np.hypot(l1[:, 0], l1[:, 1])
    return (d1 + d2) / 2


def fit_fundamental(A, B, pairs):
    # every A-detection x B-detection pairing, every 5th frame
    p1, p2 = [], []
    for i, j in pairs[::5]:
        for sa in valid(A, i):
            for sb in valid(B, j):
                k = shared(A, B, i, j, sa, sb)
                if len(k) >= MIN_SHARED:
                    p1.append(A["kp"][sa, i, k]); p2.append(B["kp"][sb, j, k])
    p1, p2 = np.concatenate(p1).astype(np.float64), np.concatenate(p2).astype(np.float64)
    F, inl = cv2.findFundamentalMat(p1, p2, cv2.USAC_MAGSAC, 3.0, 0.9999, 200000)
    print(f"fundamental: {inl.sum()} of {len(p1)} candidate points agree")
    return F


def match_people(A, B, pairs, F):
    """[(i, j, sa, sb)] per frame: the pairing with the lowest epipolar error."""
    out = []
    for i, j in pairs:
        cands = []
        for sa in valid(A, i):
            for sb in valid(B, j):
                k = shared(A, B, i, j, sa, sb)
                if len(k) >= MIN_SHARED:
                    e = np.median(epi_err(F, A["kp"][sa, i, k], B["kp"][sb, j, k]))
                    if e < PAIR_ERR_PX:
                        cands.append((e, sa, sb))
        used_a, used_b = set(), set()
        for e, sa, sb in sorted(cands):
            if sa not in used_a and sb not in used_b:
                out.append((i, j, sa, sb)); used_a.add(sa); used_b.add(sb)
    return out


def correspondences(A, B, matches, step=1):
    p1, p2 = [], []
    for i, j, sa, sb in matches[::step]:
        k = shared(A, B, i, j, sa, sb)
        p1.append(A["kp"][sa, i, k]); p2.append(B["kp"][sb, j, k])
    return np.concatenate(p1).astype(np.float64), np.concatenate(p2).astype(np.float64)


def K(f, c):
    return np.array([[f, 0, c["w"] / 2], [0, f, c["h"] / 2], [0, 0, 1.0]])


def triangulate(KA, KB, R, t, p1, p2):
    PA = KA @ np.hstack([np.eye(3), np.zeros((3, 1))])
    PB = KB @ np.hstack([R, t])
    Xh = cv2.triangulatePoints(PA, PB, p1.T, p2.T)
    return (Xh[:3] / Xh[3]).T


def reproj(X, Kc, R, t, p):
    x = (X @ R.T + t.T) @ Kc.T
    return np.linalg.norm(x[:, :2] / x[:, 2:] - p, axis=1)


def video_focal(path, offset, t0, t1, n=7):
    """(focal px, up direction in camera coords or None) from vanishing
    points, median over n frames of the window."""
    cap = cv2.VideoCapture(str(path))
    fs, frames = [], []
    for t in np.linspace(t0, t1, n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (t - offset) * 1000)
        ok, fr = cap.read()
        if ok:
            f, d = vanishing.focal(fr)
            if f:
                fs.append(f)
                frames.append((d["raw"], fr.shape))
    if not fs:
        raise SystemExit(f"no vanishing points found in {path}")
    f = float(np.median(fs))
    dirs = [d for v, sh in frames for d in vanishing.directions(v, f, sh[1], sh[0])]
    return f, dirs


def people_3d(A, B, matches, KA, KB, R, t, slots=None):
    """{A frame index: [26x3 array with NaN]}; if `slots` is a dict, it gets
    {A frame index: [camera A pose slot of each person]} in the same order."""
    out = {}
    for i, j, sa, sb in matches:
        k = shared(A, B, i, j, sa, sb)
        X = np.full((26, 3), np.nan)
        X[k] = triangulate(KA, KB, R, t, A["kp"][sa, i, k].astype(np.float64),
                           B["kp"][sb, j, k].astype(np.float64))
        # drop points that reproject badly (wrong keypoint in one view)
        e = np.maximum(reproj(X[k], KA, np.eye(3), np.zeros((3, 1)), A["kp"][sa, i, k]),
                       reproj(X[k], KB, R, t, B["kp"][sb, j, k]))
        X[k[e > 15]] = np.nan
        out.setdefault(i, []).append(X)
        if slots is not None:
            slots.setdefault(i, []).append(sa)
    return out


def body_scale(people):
    ratios = []
    for Xs in people.values():
        for X in Xs:
            for (a, b), L in SEGMENTS.items():
                d = np.linalg.norm(X[a] - X[b])
                if np.isfinite(d) and d > 0:
                    ratios.append(L / d)
    return float(np.median(ratios)), len(ratios)


def floor_frame(people, R, up_given=None):
    """Rotation (rows: world axes in A's frame) and origin putting the floor at
    y=0, y up. Up is the direction from ankles to neck of people standing
    (long, and roughly along the cameras' own up); the floor is at their feet.
    A person lying on the table is long too, but horizontal, so it's left out."""
    up0 = np.array([0, -1.0, 0]) + R.T @ np.array([0, -1.0, 0])
    up0 /= np.linalg.norm(up0)
    vs, feet = [], []
    for Xs in people.values():
        for X in Xs:
            ank = X[[15, 16]][np.isfinite(X[[15, 16]]).all(1)]
            if not np.isfinite(X[18]).all() or not len(ank):
                continue
            v = X[18] - ank.mean(0)
            L = np.linalg.norm(v)
            if L > 0.9 and v @ up0 / L > np.cos(np.radians(40)):
                vs.append(v / L)
                feet.append(X[FEET][np.isfinite(X[FEET]).all(1)])
    if up_given is not None:
        up = up_given
        pose_up = np.mean(vs, 0) / np.linalg.norm(np.mean(vs, 0)) if vs else None
        if pose_up is not None:
            print(f"standing people's 'up' differs from the room's by "
                  f"{np.degrees(np.arccos(np.clip(up @ pose_up, -1, 1))):.1f} deg")
    else:
        if len(vs) < 20:
            raise SystemExit(f"only {len(vs)} standing poses and no vertical vanishing point: no floor")
        up = np.mean(vs, 0)
        up /= np.linalg.norm(up)
    lows = [f @ up for f in feet if len(f)]
    floor = float(np.median([l.min() for l in lows]))
    z0 = np.array([0, 0, 1.0]) - up * up[2]
    z0 /= np.linalg.norm(z0)
    Rw = np.stack([np.cross(up, z0), up, z0])
    return Rw, up * floor, len(vs)


def shared_up(dirs_a, dirs_b, people, cams, tol=np.radians(6)):
    """The room's vertical, in A's frame, from the vanishing directions either
    camera sees (B's rotated into A's frame). Both cameras hang high and the
    people are below both of them, so up is the direction that puts the
    lower of the two cameras furthest above the people; a floor direction
    always leaves one camera 'below', as they sit on opposite sides."""
    allv = [(d, n) for d, n in dirs_a] + [(d, n) for d, n in dirs_b]
    pts = np.array([X[k] for Xs in people.values() for X in Xs for k in range(26) if np.isfinite(X[k]).all()])
    centre = np.median(pts, 0)
    best = None
    for d0, _ in allv:
        sup = [(d if d @ d0 > 0 else -d, n) for d, n in allv if abs(d @ d0) > np.cos(tol)]
        m = sum(d * n for d, n in sup)
        m /= np.linalg.norm(m)
        if np.mean([(c - centre) @ m for c in cams]) < 0:
            m = -m
        low = min((c - centre) @ m for c in cams)
        if best is None or low > best[0]:
            best = (low, m)
    if best is None or best[0] <= 0:
        print("vanishing points: no direction puts both cameras above the people; using standing people")
        return None
    print(f"vertical from vanishing points: lower camera {best[0]:.2f} m above the people's middle")
    return best[1]


def standing(W):
    """True/False/None: is this person (world coords) standing?"""
    ank = W[[15, 16]][np.isfinite(W[[15, 16]]).all(1)]
    if not np.isfinite(W[18]).all() or not len(ank):
        hip = W[[11, 12, 19], 1]
        hip = hip[np.isfinite(hip)]
        return None if not len(hip) else bool(hip.mean() < 0.55)
    v = W[18] - ank.mean(0)
    return bool(v[1] > 0.9 * np.linalg.norm(v) * np.cos(np.radians(40)) and v[1] > 0.9)


def track_3d(frames, max_jump=0.6, max_gap=10):
    """Give 3D people stable ids over time: join each to the nearest person of
    the last few frames (body centre within max_jump metres), then smooth each
    keypoint over 3 samples. Ids can still swap when people pass close."""
    def centre(pts):
        q = [p for k, p in enumerate(pts) if p and k in (5, 6, 11, 12, 17, 18, 19)]
        return None if not q else np.mean(q, 0)
    last, nxt = {}, 0                     # id -> (frame index, centre)
    for fi, ppl in enumerate(frames):
        used = set()
        for p in sorted(ppl, key=lambda p: -sum(1 for q in p["pts"] if q)):
            c = centre(p["pts"])
            best = None
            if c is not None:
                for k, (lf, lc) in last.items():
                    if k in used or fi - lf > max_gap:
                        continue
                    d = np.linalg.norm(c - lc)
                    if d < max_jump and (best is None or d < best[0]):
                        best = (d, k)
            if best is None:
                k, nxt = nxt, nxt + 1
            else:
                k = best[1]
            p["id"] = k
            used.add(k)
            if c is not None:
                last[k] = (fi, c)
    # smooth: average each keypoint with the same person's previous and next sample
    by_id = {}
    for fi, ppl in enumerate(frames):
        for p in ppl:
            by_id.setdefault(p["id"], {})[fi] = p
    for seq in by_id.values():
        raw = {fi: [None if q is None else np.array(q) for q in p["pts"]] for fi, p in seq.items()}
        for fi, p in seq.items():
            out = []
            for k in range(26):
                vals = [raw[f][k] for f in (fi - 1, fi, fi + 1) if f in raw and raw[f][k] is not None]
                out.append(None if raw[fi][k] is None else [round(float(v), 3) for v in np.mean(vals, 0)])
            p["pts"] = out
    # number ids by how long each person is seen, so 0 and 1 are the main two
    order = sorted(by_id, key=lambda k: -len(by_id[k]))
    rank = {k: r for r, k in enumerate(order)}
    for ppl in frames:
        for p in ppl:
            p["id"] = rank[p["id"]]
    return frames


def hand_labels(case, cam):
    """who(slot, frame) on camera `cam` from demo/labels/<case>.json, via the
    same tracker build_case.py uses; "" where nobody is named."""
    path = ROOT / "demo" / "labels" / f"{case}.json"
    cfg = json.loads(path.read_text()).get(cam) if path.exists() else None
    if not cfg:
        return lambda s, i: ""
    import build_case, tracks                         # noqa: E401
    rc = build_case.tracklet_roles(tracks.track(pose.load(ROOT / "data" / "pose" / case / f"{cam}.npz")), cfg)
    names = build_case.SKEL_ROLES
    return lambda s, i: "" if rc[s, i] <= 0 or names[rc[s, i]] == "person" else names[rc[s, i]]


def name_tracks(frames):
    """Each 3D track takes the hand label most of its frames had (the tracker
    can swap ids when people pass close; this keeps one name per track)."""
    votes = {}
    for ppl in frames:
        for p in ppl:
            if p["who"]:
                v = votes.setdefault(p["id"], {})
                v[p["who"]] = v.get(p["who"], 0) + 1
    for ppl in frames:
        for p in ppl:
            v = votes.get(p["id"])
            p["who"] = max(v, key=v.get) if v else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case")
    ap.add_argument("--from", dest="t0", type=float, required=True)
    ap.add_argument("--to", dest="t1", type=float, required=True)
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{ROOT / 'manifest.sqlite'}?mode=ro", uri=True)
    (ca, oa, va), (cb, ob, vb) = db.execute(
        "SELECT camera, sync_offset_s, filepath FROM videos WHERE case_id=? ORDER BY camera",
        (args.case,)).fetchall()[:2]
    A, B = load(args.case, ca, oa or 0.0), load(args.case, cb, ob or 0.0)
    who_a = hand_labels(args.case, ca)
    pairs = frame_pairs(A, B, args.t0, args.t1)
    print(f"{len(pairs)} synced frame pairs, {args.t0:.0f}-{args.t1:.0f} s")
    F = fit_fundamental(A, B, pairs)
    for _ in range(2):                  # match people, refit, match again
        matches = match_people(A, B, pairs, F)
        p1, p2 = correspondences(A, B, matches)
        F, inl = cv2.findFundamentalMat(p1, p2, cv2.USAC_MAGSAC, 2.0, 0.9999, 200000)
        inl = inl.ravel().astype(bool)
    print(f"{len(matches)} person matches; refit on {inl.sum()} of {len(p1)} points")
    (fa, dirs_a), (fb, dirs_b) = (video_focal(ROOT / v, o or 0.0, args.t0, args.t1) for v, o in ((va, oa), (vb, ob)))
    KA, KB = K(fa, A), K(fb, B)
    n1 = cv2.undistortPoints(p1[inl].reshape(-1, 1, 2), KA, None).reshape(-1, 2)
    n2 = cv2.undistortPoints(p2[inl].reshape(-1, 1, 2), KB, None).reshape(-1, 2)
    E, m = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.USAC_MAGSAC, 1.5 / fa, 0.9999, 100000)
    _, R, t, _ = cv2.recoverPose(E, n1, n2, np.eye(3), mask=m.copy())
    F = np.linalg.inv(KB).T @ E @ np.linalg.inv(KA)
    X = triangulate(KA, KB, R, t, p1[inl], p2[inl])
    err = float(np.median(np.r_[reproj(X, KA, np.eye(3), np.zeros((3, 1)), p1[inl]), reproj(X, KB, R, t, p2[inl])]))
    front = float(((X[:, 2] > 0) & ((X @ R.T + t.T)[:, 2] > 0)).mean())
    print(f"focal: camera {ca} {fa:.0f} px ({np.degrees(2 * np.arctan(A['w'] / 2 / fa)):.0f} deg wide), "
          f"camera {cb} {fb:.0f} px ({np.degrees(2 * np.arctan(B['w'] / 2 / fb)):.0f} deg wide); "
          f"essential-matrix inliers {int(m.sum())}/{len(m)}; median reprojection error {err:.1f} px; "
          f"{100 * front:.0f}% of points in front of both cameras")
    matches = match_people(A, B, pairs, F)
    slots = {}
    people = people_3d(A, B, matches, KA, KB, R, t, slots)
    s, n = body_scale(people)
    cams = [np.zeros(3), (-R.T @ t).ravel()]
    for Xs in people.values():
        for X in Xs:
            X *= s
    cams = [c * s for c in cams]
    vp_up = shared_up(dirs_a, [(R.T @ d, n) for d, n in dirs_b], people, cams)
    Rw, c0, n_up = floor_frame(people, R, vp_up)
    to_world = lambda X: (X - c0) @ Rw.T                                  # noqa: E731
    cam_out = []
    for cam, C, Rc, Kc, src in ((ca, cams[0], np.eye(3), KA, A), (cb, cams[1], R, KB, B)):
        cam_out.append({"camera": cam, "pos": to_world(C[None])[0].round(3).tolist(),
                        "R": (Rc @ Rw.T).round(5).tolist(),   # world -> camera rotation
                        "f": round(float(Kc[0, 0]), 1), "w": src["w"], "h": src["h"]})
    print(f"scale from {n} body segments: camera {ca} at height {cam_out[0]['pos'][1]:.2f} m, "
          f"camera {cb} at {cam_out[1]['pos'][1]:.2f} m, {np.linalg.norm(cams[1] - cams[0]):.2f} m apart; "
          f"up and floor from {n_up} standing poses")
    frames = []
    done = np.array(sorted(people))
    for g in np.arange(args.t0, args.t1, 1 / OUT_HZ):
        i = int(done[np.argmin(np.abs(A["t"][done] - g))])     # nearest reconstructed frame
        if abs(A["t"][i] - g) > 0.1:
            frames.append([]); continue
        ppl = []
        for X, sa in zip(people[i], slots[i]):
            W = to_world(X)
            st = standing(W)
            role = "" if st is None else ("standing" if st else "on table")
            ppl.append({"role": role, "who": who_a(sa, i), "pts": [None if not np.isfinite(p).all() else [round(float(v), 3) for v in p]
                                               for p in W]})
        frames.append(ppl)
    frames = track_3d(frames)
    name_tracks(frames)
    heights = [max(p[1] for p in pp["pts"] if p) for fr in frames for pp in fr
               if pp["role"] == "standing" and pp["pts"][17]]
    if heights:
        print(f"standing person's head-top height: median {np.median(heights):.2f} m")
    out = ROOT / "data" / "demo" / "cases" / args.case / "threed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "t0": args.t0, "hz": OUT_HZ, "frames": frames, "cameras": cam_out,
        "fit": {"reprojection_px": round(err, 2), "segments": n, "standing_poses": n_up},
        "note": "Uncalibrated: cameras self-calibrated from people's keypoints; scale from typical body "
                "proportions; floor fitted to feet. Shape about right, size roughly right."},
        separators=(",", ":")))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
