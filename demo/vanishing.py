"""Focal length from a frame's vanishing points (room lines: floor tiles,
door frames, wall edges). Two vanishing points of perpendicular directions,
v1 and v2, with the principal point c at the image centre, give
f^2 = -(v1 - c) . (v2 - c). Used by fake3d.py in place of a calibration."""
import cv2
import numpy as np

MIN_LEN = 40
ANGLE_TOL = np.radians(1.5)


def segments(gray):
    lines = cv2.createLineSegmentDetector().detect(gray)[0]
    if lines is None:
        return np.zeros((0, 4))
    L = lines.reshape(-1, 4)
    return L[np.hypot(L[:, 2] - L[:, 0], L[:, 3] - L[:, 1]) > MIN_LEN]


def _consistent(L, v):
    """Which segments point at vanishing point v (homogeneous)."""
    mid = (L[:, :2] + L[:, 2:]) / 2
    d = L[:, 2:] - L[:, :2]
    if abs(v[2]) > 1e-9:
        to = v[:2] / v[2] - mid
    else:
        to = np.tile(v[:2], (len(L), 1))
    cos = np.abs((d * to).sum(1)) / (np.linalg.norm(d, axis=1) * np.linalg.norm(to, axis=1) + 1e-9)
    return cos > np.cos(ANGLE_TOL)


def vanishing_points(L, n=3, iters=4000, seed=0):
    rng = np.random.default_rng(seed)
    h = np.c_[L[:, :2], np.ones(len(L))]
    g = np.c_[L[:, 2:], np.ones(len(L))]
    lines = np.cross(h, g)
    length = np.hypot(L[:, 2] - L[:, 0], L[:, 3] - L[:, 1])
    left = np.ones(len(L), bool)
    vps = []
    for _ in range(n):
        idx = np.flatnonzero(left)
        if len(idx) < 10:
            break
        best = (0, None, None)
        for _ in range(iters):
            a, b = rng.choice(idx, 2, replace=False)
            v = np.cross(lines[a], lines[b])
            if not np.isfinite(v).all() or np.linalg.norm(v) == 0:
                continue
            inl = _consistent(L, v) & left
            score = length[inl].sum()
            if score > best[0]:
                best = (score, v, inl)
        _, v, inl = best
        # refine: least-squares point closest to all inlier lines
        A = lines[inl] / np.linalg.norm(lines[inl][:, :2], axis=1, keepdims=True)
        v = np.linalg.svd(A)[2][-1]
        vps.append((v, int(inl.sum())))
        left &= ~inl
    return vps


def focal(frame, fov=(25, 100)):
    """(f, details): median of the focal lengths implied by perpendicular
    pairs of the dominant vanishing points, within a plausible field of view."""
    h, w = frame.shape[:2]
    c = np.array([w / 2, h / 2])
    vps = vanishing_points(segments(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
    lo, hi = (w / 2 / np.tan(np.radians(x / 2)) for x in (fov[1], fov[0]))
    fs = []
    for (v1, n1), (v2, n2) in [(vps[i], vps[j]) for i in range(len(vps)) for j in range(i + 1, len(vps))]:
        if abs(v1[2]) < 1e-9 or abs(v2[2]) < 1e-9:
            continue
        f2 = -((v1[:2] / v1[2] - c) @ (v2[:2] / v2[2] - c))
        if f2 > 0 and lo <= np.sqrt(f2) <= hi:
            fs.append(float(np.sqrt(f2)))
    pts = [(None if abs(v[2]) < 1e-9 else (v[:2] / v[2]).round(0).tolist(), n) for v, n in vps]
    return (float(np.median(fs)) if fs else None), {"vanishing_points": pts, "focals": fs, "raw": vps}


def directions(vps, f, w, h):
    """Unit 3D directions (camera coords) of the vanishing points."""
    Kinv = np.linalg.inv(np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]]))
    out = []
    for v, n in vps:
        d = Kinv @ v
        out.append((d / np.linalg.norm(d), n))
    return out
