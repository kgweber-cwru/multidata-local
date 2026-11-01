"""Stage: pose -- 2D keypoints per camera with rtmlib, HALPE_26 (pipeline doc §9).

Runs in the **md-pose** env (rtmlib + onnxruntime-gpu; no torch needed).

Model: rtmlib's `BodyWithFeet` models -- a YOLOX person detector and an
RTMPose network trained on HALPE_26. That is the 17 COCO body joints, in the
COCO order (nose=0 ... right ankle=16), plus head top, neck, mid-hip and six
foot points (`KEYPOINTS`). Only five points are on the face (nose, eyes,
ears): enough for rough head direction, deliberately not a face mesh. The
same keypoint set is Pose2Sim's default, so its later steps accept it as is.

Tracker is OFF: slot `s` is the s-th detection in that frame, not the same
person across frames. Identity is solved later, in 3D.

Output per camera: `data/pose/<case_id>/<camera>.npz` (`save()`/`load()`):

  keypoint        (M, T, 26, 2)  pixel x, y         stored float16
  keypoint_score  (M, T, 26)     0-1 confidence     stored float16
  bbox            (M, T, 4)      detector box x0 y0 x1 y1, float16
  frame_ms        (T,)           OpenCV's time for each frame it returned,
                                 float64 -- which file frames pose saw (see
                                 `sync.pose_reference_times`); OpenCV skips some
  plus metadata: frame_dir, total_frames, img_shape, keypoint_set, models,
  mode, detect_every, max_persons, rtmlib_version.

M = `MAX_PERSONS` slots per frame; a frame with fewer detections leaves the
rest at zero. 8, not 4: mirror reflections are detected as people, and with 4
slots they pushed real people out.
"""
import logging
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from tqdm import tqdm

log = logging.getLogger(__name__)

KEYPOINT_SET = "halpe26"
KEYPOINTS = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
    "head", "neck", "hip",
    "left_big_toe", "right_big_toe", "left_small_toe", "right_small_toe",
    "left_heel", "right_heel",
]
MAX_PERSONS = 8
KPT_THR = 0.3

_cuda_dlls_preloaded = False  # preload_dlls() is process-wide; once is enough

# One fixed BGR color per slot, so "P0" is the same hue across the whole video.
SLOT_COLORS = [(0, 0, 255), (0, 200, 0), (255, 128, 0), (255, 0, 255),
               (0, 255, 255), (255, 255, 0), (128, 0, 255), (0, 128, 255)]


def default_device():
    """'cuda' if onnxruntime can use the GPU, else 'cpu'."""
    return "cuda" if "CUDAExecutionProvider" in ort.get_available_providers() else "cpu"


def load_models(mode="balanced", device=None):
    """(detector, pose_model, info) for HALPE_26 at `mode`.

    A CUDA device needs `onnxruntime.preload_dlls()` first: pip's
    onnxruntime-gpu doesn't put the CUDA/cuDNN libraries on the loader's
    path, and without it the CUDA provider silently fails and runs on CPU.
    The detector runs on CPU only on `mps` (CoreML can't handle YOLOX's
    dynamic NMS output shape); on CUDA both run on the GPU.
    """
    import rtmlib
    from rtmlib import YOLOX, RTMPose, BodyWithFeet

    global _cuda_dlls_preloaded
    device = device or default_device()
    if device == "cuda" and not _cuda_dlls_preloaded:
        ort.preload_dlls(directory="")
        _cuda_dlls_preloaded = True
    det_device = "cpu" if device == "mps" else device

    cfg = BodyWithFeet.MODE[mode]
    det = YOLOX(cfg["det"], model_input_size=cfg["det_input_size"],
                backend="onnxruntime", device=det_device)
    pose_model = RTMPose(cfg["pose"], model_input_size=cfg["pose_input_size"],
                         to_openpose=False, backend="onnxruntime", device=device)
    info = {"mode": mode, "device": device, "det_device": det_device,
            "det_model": Path(cfg["det"]).stem, "pose_model": Path(cfg["pose"]).stem,
            "rtmlib_version": getattr(rtmlib, "__version__", "")
            or _dist_version("rtmlib")}
    return det, pose_model, info


def _dist_version(name):
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return ""


def extract(video_path, out_path=None, max_persons=MAX_PERSONS, mode="balanced",
            device=None, detect_every=1, profile=False, start_frame=0, max_frames=None):
    """Run detection + HALPE_26 pose over every frame of one video.

    Returns `(entry, details)`: the entry dict described in the module
    docstring, and a dict of what the run cost and saw (timings, frame and
    person counts), which run_stage.py records in `stage_runs`.

    The pose network runs only on frames where the detector found someone --
    most frames are an empty room, and rtmlib's all-in-one wrappers would
    otherwise invent a whole-frame "person" there.

    `detect_every` N: run the detector only every Nth frame and reuse its
    boxes in between (the pose network still runs every frame). Detection is
    ~60% of the time on the RTX 5080, so this is the speed lever; a reused box
    lags a fast mover, so check an overlay before using it for a batch.
    `profile` only adds a log line -- timings are always collected.

    `start_frame` / `max_frames` process one clip (for tests): frames before
    `start_frame` are decoded and skipped, so frame k of the clip is frame
    `start_frame + k` of a full run.
    """
    det, pose_model, info = load_models(mode, device)

    cap = cv2.VideoCapture(str(video_path))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None
    for _ in range(start_frame):
        if not cap.grab():
            break
    if max_frames is not None:
        total = max_frames

    frames, frame_ms = [], []      # per frame: (keypoints, scores, bboxes)
    timings = {"decode_s": 0.0, "detect_s": 0.0, "pose_s": 0.0}
    last_bboxes = np.empty((0, 4))
    i = 0
    t_start = time.perf_counter()
    with tqdm(total=total, unit="frame", disable=None) as pbar:
        while max_frames is None or i < max_frames:
            t0 = time.perf_counter()
            ok, frame = cap.read()
            if not ok:
                break
            frame_ms.append(cap.get(cv2.CAP_PROP_POS_MSEC))
            t1 = time.perf_counter()
            if i % detect_every == 0:
                last_bboxes = det(frame)
            bboxes = last_bboxes
            t2 = time.perf_counter()
            if len(bboxes):
                kp, sc = pose_model(frame, bboxes=bboxes)
                frames.append((kp, sc, np.asarray(bboxes)))
            else:
                frames.append(None)
            t3 = time.perf_counter()
            timings["decode_s"] += t1 - t0
            timings["detect_s"] += t2 - t1
            timings["pose_s"] += t3 - t2
            i += 1
            pbar.update(1)
    cap.release()
    wall = time.perf_counter() - t_start

    T, V, M = len(frames), len(KEYPOINTS), max_persons
    if T == 0:
        raise ValueError(f"OpenCV decoded no frames from {video_path}")
    if all(f is None for f in frames):
        # A real answer, not a failure: some cameras watch an empty corner.
        log.warning("%s: nobody detected in any frame; writing an empty file", video_path)

    kpts = np.zeros((M, T, V, 2), dtype=np.float32)
    scores = np.zeros((M, T, V), dtype=np.float32)
    boxes = np.zeros((M, T, 4), dtype=np.float32)
    n_det = np.zeros(T, dtype=np.int16)
    for t, f in enumerate(frames):
        if f is None:
            continue
        kp, sc, bb = f
        if kp.shape[1] != V:
            raise ValueError(f"model returned {kp.shape[1]} keypoints, expected {V}")
        n_det[t] = len(kp)
        n = min(len(kp), M)
        kpts[:n, t], scores[:n, t], boxes[:n, t] = kp[:n], sc[:n], bb[:n]

    entry = {
        "frame_dir": Path(video_path).stem,
        "total_frames": T,
        "img_shape": (height, width),
        "keypoint_set": KEYPOINT_SET,
        "keypoint": kpts,
        "keypoint_score": scores,
        "bbox": boxes,
        "frame_ms": np.asarray(frame_ms, dtype=np.float64),
        "models": f"{info['det_model']} + {info['pose_model']}",
        "mode": mode,
        "detect_every": detect_every,
        "max_persons": M,
        "rtmlib_version": info["rtmlib_version"],
    }
    details = {
        **{k: round(v, 2) for k, v in timings.items()},
        "loop_s": round(wall, 2),
        "frames": T,
        "fps_processed": round(T / wall, 2) if wall else None,
        "frames_with_people": int((n_det > 0).sum()),
        "mean_detections": round(float(n_det.mean()), 3),
        "max_detections": int(n_det.max()),
        "frames_over_cap": int((n_det > M).sum()),
        **{k: info[k] for k in ("device", "det_device", "det_model", "pose_model",
                                "rtmlib_version")},
    }
    if details["frames_over_cap"]:
        log.warning("%s: %d frames had more than %d detections; extras dropped",
                    video_path, details["frames_over_cap"], M)
    if profile:
        log.info("profile: %s", details)
    if out_path is not None:
        save(entry, out_path)
        log.info("wrote %s  (M=%d, T=%d, V=%d)", out_path, M, T, V)
    return entry, details


_ARRAYS = ("keypoint", "keypoint_score", "bbox")
_META = ("frame_dir", "total_frames", "img_shape", "keypoint_set", "models", "mode",
         "detect_every", "max_persons", "rtmlib_version")


def save(entry, out_path):
    """Write an entry as compressed `.npz`: keypoints, scores and boxes as
    float16, frame times float64.

    float16 spacing is 1.0 between 1024 and 2048, so coordinates inside a
    1920-wide frame lose at most 0.5 px; scores (0-1) far less. Empty slots
    are zeros and compress to almost nothing.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        str(out_path),
        **{k: np.asarray(entry[k]).astype(np.float16) for k in _ARRAYS},
        frame_ms=np.asarray(entry["frame_ms"], dtype=np.float64),
        **{k: np.array(entry[k]) for k in _META},
    )


def load(path):
    """Read an entry written by `save()`. Arrays come back float32 (frame
    times float64), so callers never do float16 math."""
    with np.load(Path(path)) as z:
        entry = {k: z[k].astype(np.float32) for k in _ARRAYS}
        entry["frame_ms"] = z["frame_ms"].astype(np.float64)
        for k in _META:
            v = z[k]
            entry[k] = (tuple(int(x) for x in v) if k == "img_shape"
                        else v.item() if v.ndim == 0 else v)
        entry["frame_dir"] = str(entry["frame_dir"])
        entry["keypoint_set"] = str(entry["keypoint_set"])
    return entry


def presence(entry):
    """Per-slot fraction of frames that carry a detection (sanity check)."""
    return (entry["keypoint_score"] > 0).any(axis=2).mean(axis=1)


def render_overlay(video_path, entry, out_path, kpt_thr=KPT_THR):
    """Draw slot-colored skeletons + P<slot> labels onto a copy of the video.

    Eyeball check: slots are not identities (no tracker), so watching one
    label jump between bodies is expected.
    """
    from rtmlib import draw_skeleton

    kp, sc = entry["keypoint"], entry["keypoint_score"]
    M, T = kp.shape[:2]

    cap = cv2.VideoCapture(str(video_path))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (width, height))
    t = 0
    while t < T:
        ok, frame = cap.read()
        if not ok:
            break
        for s in range(M):
            visible = sc[s, t] > kpt_thr
            if not visible.any():
                continue
            # draw_skeleton picks the skeleton from shape[1] of a batched
            # (N, V, C) array, so add an instance axis just for drawing.
            frame = draw_skeleton(frame, kp[s, t][None], sc[s, t][None],
                                  kpt_thr=kpt_thr, radius=3, line_width=2)
            xs, ys = kp[s, t, visible, 0], kp[s, t, visible, 1]
            cv2.putText(frame, f"P{s}", (int(xs.min()), int(ys.min()) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, SLOT_COLORS[s % len(SLOT_COLORS)],
                        2, cv2.LINE_AA)
        writer.write(frame)
        t += 1
    cap.release()
    writer.release()
    log.info("wrote %s  (%d frames, M=%d)", out_path, t, M)
    return out_path
