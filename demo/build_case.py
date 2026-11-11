#!/usr/bin/env python
"""Build one case's bundle for the encounter explorer (demo/app).

    python demo/build_case.py 261456 --offset 31=0.0
    python demo/build_case.py 261498
    python demo/build_case.py 261510 --machine          # no human transcript yet
    python demo/build_case.py 261456 --skip-video       # data only (fast)

md-pose env (OpenCV, ffmpeg). Writes data/demo/cases/<case>/ (gitignored: it
holds the case's video and words):

  meta.json           case, room, cameras, offsets, camera views, roles
  lanes.json          transcript, pitch, loudness, motion, on the reference clock
  skel_<cam>.bin      skeletons at 15 fps for the browser to draw (Int16)
  <cam>.mp4           browser-ready video (720p, 30 fps; sound on the audio camera)
  <cam>_pix.mp4       the same with faces pixelated

Transcript: the case's gold .eaf, else a human pass-1 .eaf from its kit, else
(--machine, or nothing human exists) the pipeline transcript with diarized
speakers. Roles for motion and skeleton colours come from
demo/labels/<case>.json (tracklet ids from demo/tracks.py; see that file);
without it everyone is "person". For a machine transcript, "speakers" there
names the diarized voices ({"SPEAKER_00": "PRECEPTOR", ...}). A camera with "head_direction": true there
also gets the head-direction lane (learner and patient; only meaningful on a
camera that sees faces from the side or front, not from overhead). Bookmarks: demo/bookmarks/<case>.json.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "demo"))

from multidata import pose  # noqa: E402
from multidata.tools import which  # noqa: E402
import tracks  # noqa: E402

DATA = ROOT / "data"
OUT = DATA / "demo" / "cases"
ROLES = ["LEARNER", "PATIENT", "PRECEPTOR", "ANNOUNCEMENT", "OUTSIDE_ROOM"]
SKEL_ROLES = ["", "LEARNER", "PATIENT", "PRECEPTOR", "person"]   # code -> role, 0 = empty
SKEL_FPS = 15
SKEL_PEOPLE = 4
LANE_HZ = 10          # pitch / loudness samples per second
MOTION_HZ = 4
OUT_W, OUT_H, OUT_FPS = 1280, 720, 30
HEAD = [0, 1, 2, 3, 4, 17]


# --- inputs -------------------------------------------------------------------

def case_info(case):
    db = sqlite3.connect(f"file:{ROOT / 'manifest.sqlite'}?mode=ro", uri=True)
    room, audio_cam = db.execute("SELECT room_number, audio_camera FROM recordings WHERE recording_id=?",
                                 (case,)).fetchone()
    vids = db.execute("SELECT camera, filepath, duration_s, sync_offset_s FROM videos "
                      "WHERE recording_id=? ORDER BY camera", (case,)).fetchall()
    labels = {(c, v): lab for c, v, lab in db.execute("SELECT camera, view_id, label FROM camera_views")}
    return room, audio_cam, vids, labels


def find_eaf(case):
    gold = DATA / "gold" / case / f"{case}.gold.eaf"
    if gold.exists():
        return gold, "gold (two passes, adjudicated)"
    for kit in sorted((ROOT / "annotation" / "kits" / case).glob("*/kit.json")):
        k = json.loads(kit.read_text())
        eaf = kit.parent / k["eaf"]
        if k.get("pass1_source") == "seeded" or not eaf.exists():
            continue                   # a seeded file may still be mostly machine text
        if len(read_eaf(eaf)) > 20:
            return eaf, "human pass 1 (one annotator, not adjudicated)"
    return None, None


def read_eaf(path):
    """[(start_s, end_s, role, text)] sorted, speaker tiers only."""
    root = ET.parse(path).getroot()
    slots = {s.get("TIME_SLOT_ID"): int(s.get("TIME_VALUE", 0)) for s in root.iter("TIME_SLOT")}
    out = []
    for tier in root.iter("TIER"):
        role = tier.get("TIER_ID")
        if role not in ROLES:
            continue
        for a in tier.iter("ALIGNABLE_ANNOTATION"):
            v = a.find("ANNOTATION_VALUE")
            text = (v.text or "").strip() if v is not None else ""
            s, e = slots.get(a.get("TIME_SLOT_REF1"), 0), slots.get(a.get("TIME_SLOT_REF2"), 0)
            if text and e > s:
                out.append((s / 1000, e / 1000, role, text))
    return sorted(out)


def machine_transcript(case, cam):
    """Utterances from the pipeline transcript, speakers as diarized."""
    from multidata import seeding           # standard library only
    p = DATA / "transcripts" / case / f"{cam}_{seeding.SEED_ENGINE}_{seeding.SEED_MODEL}.json"
    utts = seeding.utterances(json.loads(p.read_text()))
    return [(a, b, spk, text) for spk, a, b, text in utts], p.name


# --- lanes --------------------------------------------------------------------

def speaker_mask(utts, t, role):
    """Boolean over times t: inside an utterance of `role` and of no other role."""
    mine = np.zeros(len(t), bool)
    other = np.zeros(len(t), bool)
    for a, b, r, _ in utts:
        sel = (t >= a) & (t < b)
        if r == role:
            mine |= sel
        else:
            other |= sel
    return mine & ~other


def _pcm(path, sr=8000, keep_gaps=False):
    """Mono samples. keep_gaps: fill timestamp gaps with silence, so sample
    k is at time k/sr on the file's own clock (a plain decode skips them)."""
    af = ["-af", "aresample=async=1:first_pts=0"] if keep_gaps else []
    b = subprocess.run([which("ffmpeg"), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                        "-ar", str(sr)] + af + ["-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(b, np.int16).astype(np.float32)


def audio_clock(wav, video, sr=8000, step=10.0, win=20.0, reach=30.0):
    """Map times in the pipeline's wav (which the transcripts, diarization and
    prosody use) to times in the encoded video.

    On a few cameras the recorded audio is shorter than the video (room 11's
    camera 16: up to 15 s over 20 min), because the camera dropped sound
    during its recording gaps without leaving silence. The wav then runs
    ahead of the picture by a growing amount. This matches the two
    soundtracks every `step` seconds and interpolates. Returns (fn, note);
    fn is None when the two already agree."""
    a, v = _pcm(wav, sr), _pcm(video, sr, keep_gaps=True)
    if abs(len(v) - len(a)) / sr < 1.0:
        return None, None
    n, pairs = int(win * sr), []
    for tw in np.arange(5.0, len(a) / sr - win, step):
        x = a[int(tw * sr):int(tw * sr) + n]
        if x.std() < 1:
            continue
        lo = int(max(tw - 2, 0) * sr)                      # the wav only runs ahead
        y = v[lo:int((tw + reach) * sr) + n]
        if len(y) <= n:
            continue
        m = len(y) + n
        c = np.fft.irfft(np.fft.rfft(y, m) * np.conj(np.fft.rfft(x, m)), m)[:len(y) - n + 1]
        k = int(c.argmax())
        yy = y[k:k + n]
        r = float(np.dot(x - x.mean(), yy - yy.mean()) / (x.std() * yy.std() * n + 1e-9))
        if r > 0.5:
            pairs.append((tw, lo / sr + k / sr))
    if len(pairs) < 5:
        return None, "transcript clock: soundtracks differ in length but couldn't be matched"
    tw, tv = np.array(pairs).T
    d = tv - tw
    keep = np.abs(d - np.convolve(np.pad(d, 2, mode="edge"), np.ones(5) / 5, "valid")) < 0.5
    keep &= np.r_[True, np.diff(d) > -0.2]              # the shift only grows
    tw, tv = tw[keep], tv[keep]
    note = (f"transcript re-timed to the video: its audio was {tv[-1] - tw[-1]:.1f} s short "
            f"by the end (camera dropped sound in recording gaps); matched at {len(tw)} points")
    shift = tv - tw
    return (lambda t: np.asarray(t, float) + np.interp(t, tw, shift)), note


def prosody_lanes(case, cam, utts, roles, duration, clock=None):
    p = DATA / "prosody" / case / f"{cam}.npz"
    if not p.exists():
        return None
    z = np.load(p, allow_pickle=False)
    t, f0, db = z["time_s"], z["f0_hz"], z["intensity_db"]
    if clock:
        t = clock(t)
    grid = np.arange(0, duration, 1 / LANE_HZ)
    idx = np.clip(np.searchsorted(t, grid), 0, len(t) - 1)
    bins = np.clip((t * LANE_HZ).astype(int), 0, len(grid) - 1)

    def binned(vals, mask):
        s = np.bincount(bins[mask], vals[mask], minlength=len(grid))
        n = np.bincount(bins[mask], minlength=len(grid))
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, s / n, np.nan)

    pitch = {}
    for role in roles:
        m = speaker_mask(utts, t, role) & ~np.isnan(f0)
        if m.sum() > 50:
            pitch[role] = rnd(binned(f0, m), 1)
    loud = binned(db, ~np.isnan(db))
    return {"hz": LANE_HZ, "pitch_hz": pitch, "loudness_db": rnd(loud, 1)}


def rnd(a, d):
    return [None if not np.isfinite(x) else round(float(x), d) for x in a]


# --- pose: roles, skeletons, motion -------------------------------------------

def tracklet_roles(ids, labels):
    """(M, T) role code per slot-frame from tracklet labels."""
    cfg = labels or {}
    named = {int(k): v for k, v in cfg.get("tracks", {}).items()}
    default = cfg.get("default")
    summ = tracks.summary(ids)
    min_len = cfg.get("default_min_frames", 30)
    code = {r: i for i, r in enumerate(SKEL_ROLES)}
    lut = {}
    for k, (n, _, _) in summ.items():
        role = named.get(k)
        if role is None:
            role = default if (default and n >= min_len) else "person"
        lut[k] = -1 if role == "ignore" else code[role]
    out = np.zeros(ids.shape, np.int16)
    for k, c in lut.items():
        out[ids == k] = c
    out[ids < 0] = 0
    return out


def skeletons(e, rc, offset, duration):
    """Int16 (frames, SKEL_PEOPLE, 53): role code, then 26 x,y (-1 = not confident)."""
    t_ref = e["frame_ms"] / 1000 + offset
    grid = np.arange(0, duration, 1 / SKEL_FPS)
    out = np.zeros((len(grid), SKEL_PEOPLE, 53), np.int16)
    idx = np.searchsorted(t_ref, grid)
    kp, sc = e["keypoint"], e["keypoint_score"]
    for g, i in enumerate(idx):
        if i >= len(t_ref) or (i > 0 and abs(t_ref[i - 1] - grid[g]) < abs(t_ref[i] - grid[g])):
            i -= 1
        if i < 0 or abs(t_ref[i] - grid[g]) > 0.2:
            continue
        slots = [s for s in np.flatnonzero(rc[:, i] > 0)][:SKEL_PEOPLE]
        for j, s in enumerate(slots):
            xy = np.round(kp[s, i]).astype(np.int16)
            xy[sc[s, i] <= tracks.KPT_THR] = -1
            out[g, j, 0] = rc[s, i]
            out[g, j, 1:] = xy.reshape(-1)
    return out


def motion(e, ids, rc, offset, duration, held, smooth=5):
    """{role: [...]} body movement per role, MOTION_HZ samples: mean keypoint
    speed in frame-heights per second. Each tracklet's keypoints are first
    averaged over `smooth` frames, so detector jitter doesn't read as
    movement. NaN while the camera moves or the role isn't in view."""
    kp, sc = e["keypoint"], e["keypoint_score"]
    t_all = e["frame_ms"] / 1000 + offset
    H = float(e["img_shape"][0])
    grid_n = int(duration * MOTION_HZ) + 1
    sums, counts = {}, {}
    M, T = ids.shape
    for k in np.unique(ids[ids >= 0]):
        slot, frame = np.nonzero(ids == k)
        order = np.argsort(frame)
        slot, frame = slot[order], frame[order]
        if len(frame) < smooth + 2:
            continue
        code = int(rc[slot[0], frame[0]])
        if code <= 0:
            continue
        P = kp[slot, frame].astype(np.float64)                     # n, V, 2
        C = (sc[slot, frame] > tracks.KPT_THR).astype(np.float64)  # n, V
        w = np.ones(smooth)
        num = np.stack([np.apply_along_axis(np.convolve, 0, P[..., d] * C, w, "same") for d in (0, 1)], -1)
        den = np.apply_along_axis(np.convolve, 0, C, w, "same")
        Ps = num / np.maximum(den, 1)[..., None]
        good = den >= smooth - 1                                   # mostly-confident window
        t = t_all[frame]
        dt = np.diff(t)
        both = good[1:] & good[:-1]
        d = np.linalg.norm(np.diff(Ps, axis=0), axis=-1)
        n_ok = both.sum(-1)
        v = np.where(both, d, 0).sum(-1) / np.maximum(n_ok, 1) / H / np.maximum(dt, 1e-3)
        keep = (n_ok >= 5) & (np.diff(frame) <= 2) & (dt < 0.2) & held(t[1:])
        bins = np.clip((t[1:][keep] * MOTION_HZ).astype(int), 0, grid_n - 1)
        role = SKEL_ROLES[code]
        sums.setdefault(role, np.zeros(grid_n))
        counts.setdefault(role, np.zeros(grid_n))
        np.add.at(sums[role], bins, np.clip(v[keep], 0, 2))
        np.add.at(counts[role], bins, 1)
    out = {}
    for role in sums:
        if counts[role].sum() < 100:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            out[role] = rnd(np.where(counts[role] >= 2, sums[role] / counts[role], np.nan), 4)
    return out


GAZE_PAIRS = (("LEARNER", "PATIENT"), ("PATIENT", "LEARNER"))
DOWN_MIN = 0.4        # nose this far below the ears (in head lengths): looking down
TOWARD_MIN = 0.8      # cosine between face direction and the other person's head


def head_direction(e, rc, offset, duration, held):
    """{role: "0123..."} at MOTION_HZ, where each person's head points, from
    the 2D head keypoints on this camera: 1 toward the other person, 2 down,
    3 elsewhere, 0 unknown. A proxy for gaze, not eye tracking: it reads the
    face direction (ears -> nose) and how far the nose sits below the ears.
    Thresholds were set by eye on room 11's camera 16."""
    kp, sc = e["keypoint"], e["keypoint_score"]
    ok = sc > tracks.KPT_THR
    t = e["frame_ms"] / 1000 + offset
    n = int(duration * MOTION_HZ) + 1
    code = {r: i for i, r in enumerate(SKEL_ROLES)}

    def best(c, i):
        s = np.flatnonzero(rc[:, i] == c)
        return None if not len(s) else s[np.argmax(sc[s, i, :5].sum(-1))]

    out = {}
    for me, other in GAZE_PAIRS:
        votes = np.zeros((n, 4))
        for i in np.flatnonzero(held(t)):
            a, b = best(code[me], i), best(code[other], i)
            if a is None or b is None or not ok[a, i, 0]:
                continue
            ears = [kp[a, i, j] for j in (3, 4) if ok[a, i, j]]
            tgt = kp[b, i, 0] if ok[b, i, 0] else (kp[b, i, 17] if ok[b, i, 17] else None)
            if not ears or tgt is None:
                continue
            f = kp[a, i, 0] - np.mean(ears, 0)
            to = tgt - kp[a, i, 0]
            nf = np.linalg.norm(f)
            if nf < 1:
                continue
            down = f[1] / nf
            cos = f @ to / (nf * np.linalg.norm(to) + 1e-6)
            k = 2 if down > DOWN_MIN else (1 if cos > TOWARD_MIN else 3)
            votes[min(int(t[i] * MOTION_HZ), n - 1), k] += 1
        if votes.sum() > 100:
            cls = np.where(votes.sum(1) >= 2, votes.argmax(1), 0)
            out[me] = "".join(map(str, cls))
    return out


# --- video --------------------------------------------------------------------

TOWARD_3D_DEG = 35   # 3D: face direction within this of the line to the other's head
DOWN_3D_DEG = 35     # 3D: face direction this far below level (neutral is about 10 below)


def head_direction_3d(threed, duration):
    """Like head_direction, but from the 3D reconstruction (threed.json, made
    by fake3d.py with hand labels), so it works from any camera angle. Only
    covers the 3D stretch; "0" elsewhere. Face direction is ears -> nose."""
    n = int(duration * MOTION_HZ) + 1
    out = {}
    for me, other in GAZE_PAIRS:
        votes = np.zeros((n, 4))
        for k, fr in enumerate(threed["frames"]):
            a = [p for p in fr if p.get("who") == me]
            b = [p for p in fr if p.get("who") == other]
            if len(a) != 1 or len(b) != 1:
                continue
            pa, pb = a[0]["pts"], b[0]["pts"]
            ears = [pa[j] for j in (3, 4) if pa[j]]
            tgt = pb[0] or pb[17]
            if not pa[0] or not ears or not tgt:
                continue
            nose = np.array(pa[0])
            f = nose - np.mean(ears, 0)
            if np.linalg.norm(f) < 0.03:
                continue
            f /= np.linalg.norm(f)
            to = (np.array(tgt) - nose) / (np.linalg.norm(np.array(tgt) - nose) + 1e-9)
            if f @ to > np.cos(np.radians(TOWARD_3D_DEG)):
                c = 1
            elif f[1] < -np.sin(np.radians(DOWN_3D_DEG)):          # y is up
                c = 2
            else:
                c = 3
            t = threed["t0"] + k / threed["hz"]
            votes[min(int(t * MOTION_HZ), n - 1), c] += 1
        if votes.sum() > 50:
            out[me] = "".join(map(str, np.where(votes.sum(1) >= 1, votes.argmax(1), 0)))
    return out


def encode_plain(src, dst, audio):
    cmd = [which("ffmpeg"), "-y", "-loglevel", "error", "-i", str(src),
           "-vf", f"scale={OUT_W}:{OUT_H},fps={OUT_FPS}", "-c:v", "h264_nvenc", "-preset", "p5",
           "-cq", "26", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "96k"] if audio else ["-an"]
    subprocess.run(cmd + ["-movflags", "+faststart", str(dst)], check=True)


def pixelate(frame, kp, sc, slots):
    h, w = frame.shape[:2]
    for s in slots:
        m = sc[s, HEAD] > 0.1
        if m.sum() < 2:
            continue
        pts = kp[s, HEAD][m]
        cx, cy = pts.mean(0)
        r = max(float(np.linalg.norm(pts - (cx, cy), axis=1).max()) * 2.8, 22.0)
        x0, y0 = int(max(cx - r, 0)), int(max(cy - r * 1.2, 0))
        x1, y1 = int(min(cx + r, w)), int(min(cy + r * 1.2, h))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        roi = frame[y0:y1, x0:x1]
        small = cv2.resize(roi, (max((x1 - x0) // 24, 1), max((y1 - y0) // 24, 1)),
                           interpolation=cv2.INTER_AREA)
        big = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        mask = np.zeros(roi.shape[:2], np.uint8)
        cv2.ellipse(mask, (int(cx) - x0, int(cy) - y0), (int(r), int(r * 1.2)), 0, 0, 360, 255, -1)
        roi[mask > 0] = big[mask > 0]


def encode_pixelated(src, dst, e, audio, duration):
    """Faces pixelated from the pose head keypoints, re-timed to constant
    30 fps from each frame's recorded time (some cameras record a variable
    frame rate), so it lines up with the plain version."""
    kp, sc = e["keypoint"], e["keypoint_score"]
    t = e["frame_ms"] / 1000
    present = (sc > tracks.KPT_THR).sum(axis=2) >= 3
    cmd = [which("ffmpeg"), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{OUT_W}x{OUT_H}", "-r", str(OUT_FPS), "-i", "-"]
    if audio:
        cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "96k", "-shortest"]
    cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "26", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(dst)]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cap = cv2.VideoCapture(str(src))
    i, frame, out = -1, None, None
    for k in range(int(duration * OUT_FPS)):
        tk = k / OUT_FPS
        while i + 1 < len(t) and t[i + 1] <= tk + 1e-3:
            ok, f = cap.read()
            if not ok:
                break
            i += 1
            frame, out = f, None
        if frame is None:
            continue
        if out is None:
            g = frame.copy()
            pixelate(g, kp[:, i], sc[:, i], np.flatnonzero(present[:, i]))
            out = cv2.resize(g, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA).tobytes()
        ff.stdin.write(out)
    ff.stdin.close()
    ff.wait()
    cap.release()


# --- main ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case")
    ap.add_argument("--machine", action="store_true", help="use the pipeline transcript even if a human one exists")
    ap.add_argument("--offset", action="append", default=[],
                    help="CAM=SECONDS: sync offset for a camera whose audio sync failed")
    ap.add_argument("--skip-video", action="store_true")
    args = ap.parse_args()
    case = args.case
    room, audio_cam, vids, view_labels = case_info(case)
    override = {c: float(s) for c, s in (o.split("=") for o in args.offset)}
    out = OUT / case
    out.mkdir(parents=True, exist_ok=True)

    labels_path = ROOT / "demo" / "labels" / f"{case}.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else {}

    eaf, source = (None, None) if args.machine else find_eaf(case)
    if eaf:
        utts = read_eaf(eaf)
        roles = [r for r in ROLES if any(u[2] == r for u in utts)]
    else:
        utts, name = machine_transcript(case, audio_cam)
        named = labels.get("speakers") or {}
        if named:          # diarized voices named by hand: {"SPEAKER_00": "PRECEPTOR", ...}
            utts = [(a, b, named.get(r, r), t) for a, b, r, t in utts]
            source = (f"machine ({name}); voices named by hand, but diarization errors "
                      "remain" + (f": {labels['speakers_note']}" if labels.get("speakers_note") else ""))
            roles = [r for r in ROLES if any(u[2] == r for u in utts)]
            roles += sorted({u[2] for u in utts} - set(roles))
        else:
            source = f"machine ({name}; speakers as diarized, not checked)"
            roles = sorted({u[2] for u in utts})
    duration = min(v[2] for v in vids)
    bm_path = ROOT / "demo" / "bookmarks" / f"{case}.json"

    cams, motion_lanes, gaze_lanes = [], {}, {}
    for cam, path, _, offset in vids:
        note = "audio sync"
        if cam in override:
            offset, note = override[cam], "approximate: matched by movement (audio sync failed)"
        elif offset is None:
            offset, note = 0.0, "unsynced: assumed 0 (audio sync failed)"
        e = pose.load(DATA / "pose" / case / f"{cam}.npz")
        print(f"{case}/{cam}: tracking")
        ids = tracks.track(e)
        rc = tracklet_roles(ids, labels.get(cam))
        views = json.loads((DATA / "views" / case / f"{cam}.json").read_text())["stretches"]

        def held(tt, views=views, offset=offset):
            m = np.zeros(len(tt), bool)
            for s in views:
                m |= (tt >= s["t0"] + offset + 0.5) & (tt < s["t1"] + offset - 0.5)
            return m

        skeletons(e, rc, offset, duration).tofile(out / f"skel_{cam}.bin")
        motion_lanes[cam] = motion(e, ids, rc, offset, duration, held)
        if (labels.get(cam) or {}).get("head_direction"):
            gaze_lanes[cam] = head_direction(e, rc, offset, duration, held)
        h, w = (int(x) for x in e["img_shape"][:2])
        cams.append({
            "camera": cam, "offset": offset, "offset_note": note, "width": w, "height": h,
            "video": f"{cam}.mp4", "video_pix": f"{cam}_pix.mp4", "skeletons": f"skel_{cam}.bin",
            "audio": cam == audio_cam,
            "views": [{"t0": s["t0"] + offset, "t1": s["t1"] + offset,
                       "label": view_labels.get((cam, s["view"])) or (s["view"] or "moving")}
                      for s in views]})
        if not args.skip_video:
            src = ROOT / path
            print(f"{case}/{cam}: encoding video")
            encode_plain(src, out / f"{cam}.mp4", cam == audio_cam)
            print(f"{case}/{cam}: encoding pixelated video")
            encode_pixelated(src, out / f"{cam}_pix.mp4", e, cam == audio_cam, duration)

    clock, clock_note = None, None
    wav = DATA / "audio" / case / f"{audio_cam}.wav"
    if wav.exists() and (out / f"{audio_cam}.mp4").exists():
        print(f"{case}/{audio_cam}: checking the transcript's clock against the video")
        clock, clock_note = audio_clock(wav, out / f"{audio_cam}.mp4")
        if clock_note:
            print("  " + clock_note)
    if clock:
        utts = [(float(clock(a)), float(clock(b)), r, t) for a, b, r, t in utts]

    cams.sort(key=lambda c: not c["audio"])
    meta = {"case": case, "room": room, "duration_s": duration, "transcript_source": source,
            "transcript_clock": clock_note, "roles": roles, "cameras": cams, "skeleton": {"fps": SKEL_FPS, "people": SKEL_PEOPLE,
                                                         "roles": SKEL_ROLES},
            "motion_hz": MOTION_HZ,
            "bookmarks": json.loads(bm_path.read_text()) if bm_path.exists() else []}
    t3 = out / "threed.json"
    if t3.exists():
        threed = json.loads(t3.read_text())
        if any(p.get("who") for fr in threed["frames"] for p in fr):
            h3 = head_direction_3d(threed, duration)
            if h3:
                gaze_lanes = {"3D": h3, **gaze_lanes}
    lanes = {"utterances": [[round(a, 2), round(b, 2), r, t] for a, b, r, t in utts],
             "prosody": prosody_lanes(case, audio_cam, utts, roles, duration, clock),
             "motion": motion_lanes, "head": gaze_lanes}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    (out / "lanes.json").write_text(json.dumps(lanes, separators=(",", ":")))
    index = OUT / "index.json"
    built = json.loads(index.read_text()) if index.exists() else {}
    built[case] = {"room": room, "transcript_source": source, "duration_s": duration}
    index.write_text(json.dumps(built, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
