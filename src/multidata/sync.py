"""Stage: sync — put a case's camera files on one clock, for triangulation.

Two facts about the raw files make this necessary (measured 2026-10-29, see
docs/pose_parked.md "Syncing cameras"):

1. **The two files of a case start up to ~1 s apart, by a fixed amount.**
   Both carry the same audio feed (see `manifest.ensure_audio_camera`), so
   cross-correlating the audio gives the offset to the sample. Once both
   cameras are recording they stay together: across 14 cases, no measurable
   drift over 10-60 minutes.
2. **Files drop video frames**, usually one or two per recording, but camera
   16 in room 11 lost ~229 s in 324 gaps on case 261456. Pose files are
   indexed by frame number, so frame number is not time. Each frame's real
   time is its presentation timestamp in the file, which `frame_times()`
   reads.

Together: a frame `i` of camera `c` happened at reference time

    frame_time_s[i] + sync_offset_s[c]

on the clock of the case's reference camera (`cases.audio_camera`, whose own
offset is 0). `reference_times()` does that sum.

Outputs:
  - `videos.sync_offset_s`   seconds to add to this file's own time to get
                             reference time. NULL until measured.
  - `videos.sync_agreement`  fraction of audio windows agreeing with that
                             offset (1.0 on a clean case). The offset is only
                             written when it's high enough (MIN_AGREEMENT).
  - `data/sync/<case_id>/<camera>.npz` with `frame_time_s` (one per decoded
    frame, in order: entry i is pose frame i).

Measurement: GCC-PHAT on WINDOW_S-second windows every STEP_S seconds. Each
confident window gives one lag. The offset is the median, and windows count as
agreeing when within AGREE_TOL_S of it. A case whose audio has gaps (room 11)
gives scattered lags and fails, so a bad offset is never written.

numpy + stdlib only (wav via `wave`); `ffmpeg`/`ffprobe` come from PATH, as in
`audio.py` -- run in md-speech.
"""
import json
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
WINDOW_S = 10.0
STEP_S = 30.0
MAX_LAG_S = 5.0
MIN_PEAK = 15.0       # GCC peak height over the median |cc|: below this the window is silence/noise
AGREE_TOL_S = 0.005
MIN_WINDOWS = 5
MIN_AGREEMENT = 0.8


# --- audio offset ----------------------------------------------------------

def read_wav(path):
    """16-bit mono wav -> (float32 samples in [-1, 1], sample rate)."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path}: expected 16-bit mono, got "
                             f"{w.getsampwidth() * 8}-bit x{w.getnchannels()}")
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return x.astype(np.float32) / 32768.0, sr


def gcc_lag(a, b, sr, max_lag_s=MAX_LAG_S):
    """(lag_s, peak) of b relative to a by GCC-PHAT. Positive lag: a sound
    appears later in b than in a. `peak` is the correlation peak over its
    median absolute value -- a confidence score, not a probability."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    R = np.conj(np.fft.rfft(a, n)) * np.fft.rfft(b, n)
    R /= np.abs(R) + 1e-12
    cc = np.fft.irfft(R, n)
    m = min(int(max_lag_s * sr), n // 2 - 1)
    cc = np.concatenate([cc[-m:], cc[:m + 1]])
    k = int(np.argmax(cc))
    return (k - m) / sr, float(cc[k] / (np.median(np.abs(cc)) + 1e-12))


def measure_lag(ref, other, sr):
    """Lag of `other` relative to `ref` across the recording.

    Returns {"lag_s", "agreement", "windows", "lags"}; `lag_s` is None when
    fewer than MIN_WINDOWS windows were confident. Windows are compared at the
    same file time, searching +/- MAX_LAG_S around it.
    """
    win, step, pad = int(WINDOW_S * sr), int(STEP_S * sr), int(MAX_LAG_S * sr)
    lags = []
    for start in range(pad, min(len(ref), len(other)) - win - pad, step):
        a = ref[start:start + win]
        if a.std() < 1e-4:
            continue
        # b starts pad samples early, so a true lag L shows up as L + pad,
        # anywhere in [0, 2 * pad]: search that range, then undo the shift
        b = other[start - pad:start + win + pad]
        lag, peak = gcc_lag(a, b, sr, max_lag_s=2 * MAX_LAG_S)
        if peak >= MIN_PEAK and 0 <= lag <= 2 * MAX_LAG_S:
            lags.append(lag - MAX_LAG_S)
    if len(lags) < MIN_WINDOWS:
        return {"lag_s": None, "agreement": 0.0, "windows": len(lags), "lags": lags}
    lag = float(np.median(lags))
    agreement = float(np.mean(np.abs(np.array(lags) - lag) <= AGREE_TOL_S))
    return {"lag_s": lag, "agreement": agreement, "windows": len(lags), "lags": lags}


def extract_audio(video_path, out_path):
    """16 kHz mono wav, same settings as `audio.extract` (no loudnorm: the
    correlation doesn't care about level)."""
    from multidata import audio

    return audio.extract(video_path, out_path, loudnorm=False)


def offset_from_audio(ref_wav, other_wav):
    """sync_offset_s for the camera behind `other_wav` (reference = 0), plus
    the measurement. An event at time t in other's file is at t - lag in the
    reference's, so offset = -lag."""
    ref, sr = read_wav(ref_wav)
    other, sr2 = read_wav(other_wav)
    if sr != sr2:
        raise ValueError(f"sample rates differ: {sr} vs {sr2}")
    m = measure_lag(ref, other, sr)
    offset = None if m["lag_s"] is None else -m["lag_s"]
    return offset, m


# --- frame times -------------------------------------------------------------

def frame_times(video_path):
    """Presentation time (s) of every video frame, in display order -- entry i
    is the i-th frame a decoder (cv2, so pose) returns. Gaps show up as
    jumps bigger than one frame period."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(video_path)],
        check=True, capture_output=True, text=True,
    ).stdout
    t = np.array([float(x) for x in out.split() if x and x != "N/A"])
    t.sort()
    return t


def dropped(times):
    """{"gaps", "lost_s", "max_gap_s"} -- frames missing between timestamps."""
    if len(times) < 2:
        return {"gaps": 0, "lost_s": 0.0, "max_gap_s": 0.0}
    d = np.diff(times)
    period = float(np.median(d))
    big = d > 1.5 * period
    return {"gaps": int(big.sum()), "lost_s": float((d[big] - period).sum()),
            "max_gap_s": float(d.max())}


def save_frame_times(times, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, frame_time_s=times.astype(np.float64))
    return out_path


def load_frame_times(path):
    with np.load(path) as z:
        return z["frame_time_s"]


def snap_to_pts(decoder_times, pts):
    """Frame times for exactly the frames a decoder returned.

    OpenCV (and so pose) sometimes returns fewer frames than the file holds,
    usually around gaps, so `frame_times()` can be longer than a pose entry's
    T. OpenCV's own per-frame time (CAP_PROP_POS_MSEC) says which frames it
    kept, but now and then repeats a value. So: snap each decoder time to the
    nearest real timestamp, never reusing or going backwards.
    """
    pts = np.asarray(pts)
    out = np.empty(len(decoder_times), dtype=np.int64)
    k = 0
    for i, t in enumerate(decoder_times):
        j = int(np.clip(np.searchsorted(pts, t), 1, len(pts) - 1))
        j -= (t - pts[j - 1]) < (pts[j] - t)
        j = max(j, k)
        if j >= len(pts):
            raise ValueError(f"decoder returned more frames than the file has ({len(pts)})")
        out[i] = j
        k = j + 1
    return pts[out]


def reference_times(frame_time_s, sync_offset_s, n_frames=None):
    """Per-frame times on the case's reference clock.

    Pass `n_frames` (e.g. a pose entry's T) to check that the frame-indexed
    data lines up with the file's frames: a mismatch means frame i of that
    data isn't frame i of the video, and pairing by time would be wrong.
    """
    if sync_offset_s is None:
        raise ValueError("no sync offset for this camera -- run the sync stage "
                         "(or it failed: check videos.sync_agreement)")
    if n_frames is not None and n_frames != len(frame_time_s):
        raise ValueError(f"{n_frames} data frames but {len(frame_time_s)} video frames")
    return np.asarray(frame_time_s) + sync_offset_s


def nearest_frames(ref_times_a, ref_times_b, max_gap_s=None):
    """For each frame of camera a, the index of camera b's frame nearest in
    reference time, and the time difference. With `max_gap_s`, pairs further
    apart (b dropped frames there) get index -1."""
    b = np.asarray(ref_times_b)
    j = np.clip(np.searchsorted(b, ref_times_a), 1, len(b) - 1)
    j -= (ref_times_a - b[j - 1]) < (b[j] - ref_times_a)
    dt = b[j] - ref_times_a
    if max_gap_s is not None:
        j = np.where(np.abs(dt) <= max_gap_s, j, -1)
    return j, dt


def summary(m):
    """Short JSON-able record of a measurement, for logs."""
    lags = np.array(m["lags"]) if m["lags"] else np.array([])
    return json.dumps({
        "lag_s": m["lag_s"], "agreement": round(m["agreement"], 3), "windows": m["windows"],
        "lag_range_s": [float(lags.min()), float(lags.max())] if len(lags) else None,
    })


def tempdir():
    return tempfile.TemporaryDirectory(prefix="md-sync-")
