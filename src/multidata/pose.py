"""Stage: pose — rtmlib RTMPose extraction (pipeline doc §9).

Promoted from `notebooks/mmpose.ipynb` cells 2-4 of the old multidata repo.
Runs in the **md-pose** env.

Tracker is OFF, deliberately: slot `s` is just the s-th detection in that frame
and is NOT the same person across frames. Stable identity is deferred work, not
a pipeline blocker (doc §9).

Output per camera: `data/pose/<case_id>/<camera>.pkl` holding one entry dict
with `keypoint` (M, T, V, C) and `keypoint_score` (M, T, V), both float32.
`V` (133) is COCO-WholeBody: indices 0-16 are the same 17 body joints, in the
same order, as plain COCO body (nose=0, ...) -- 17-22 add feet, 23-90 face,
91-132 the two hands. Anything hardwired to `V == 17` needs updating; nothing
about the first 17 changed.
"""
import logging
import time

import cv2
import mmengine
import numpy as np
from tqdm import tqdm
import onnxruntime as ort

log = logging.getLogger(__name__)

MAX_PERSONS = 4  # fixed M slots; frames with fewer people leave slots at zero
KPT_THR = 0.3

# One fixed BGR color per slot, so "P0" is the same hue across the whole video.
SLOT_COLORS = [(0, 0, 255), (0, 200, 0), (255, 128, 0), (255, 0, 255),
               (0, 255, 255), (255, 255, 0)]


def extract(video_path, out_path=None, max_persons=MAX_PERSONS,
            mode="balanced", device=None, profile=False, detect_every=1):
    """Run RTMPose over every frame of one video -> MMAction2-style entry dict.

    Detection normally runs on every frame; the pose network only runs on
    frames where detection found someone. No identity across frames.

    `detect_every` (default 1, i.e. every frame): re-run YOLOX only every Nth
    frame, holding the last bboxes for the frames in between (still fed to
    RTMPose every frame). Confirmed via `--profile` that YOLOX-on-CPU is ~88%
    of wall-clock time and CoreML-accelerated RTMPose only ~11% (doc §9), so
    this is the actual lever for throughput -- the held bbox is a person's
    prior-frame position, not a fresh one, which very occasionally lags a fast
    move; eyeball a `render_overlay()` render at your chosen stride before
    trusting it for a full batch.

    `device` (the pose-network device) defaults to the best available
    (`multidata.device.best_torch_device` — mps > cuda > cpu) *if* torch is
    importable -- neither `env/pose.yml` nor `env/pose-nvidia.yml` installs
    torch, so in the actual `md-pose` env leaving `device` unset raises
    `ModuleNotFoundError` instead of auto-detecting. In practice `device` is
    always passed explicitly (`run_stage.py --accel-device cuda`/`mps`); don't
    rely on the auto-detect path in this env. The detector (YOLOX) is pinned
    to CPU only on `mps`: onnxruntime's CoreML EP
    can build a session for it but crashes at inference time on its dynamic
    NMS output shape (`{1,1,1,8400,8400}` vs the graph's `{1,8400}}`) — a real
    onnxruntime/CoreML limitation, confirmed on Apple Silicon. That's
    specifically a CoreML issue, not a CUDA one, so on `cuda` the detector
    also runs on the GPU. `tofino`'s permanent card is an RTX 5080 (its
    original RTX 3070 failed in 2026-07 and is not coming back). Settled
    baseline, an 18-video/~17.3h production batch (2026-09, detector + pose
    net both on CUDA, zero crashes/fallback): 1.67x realtime aggregate (mean
    1.72x per-video, range 1.38x-2.39x, stdev 0.31), decode ~4% /
    detect[cuda] ~60% / pose[cuda] ~37% of wall-clock. Detect is still the
    larger cost on this card, closer to the old CPU-bound-detector shape than
    a single short smoke-test clip suggested -- plan capacity off the batch
    aggregate (1.67x), not the smoke-test number. RTMPose has no dynamic-shape
    issue and runs cleanly under CoreML EP; rtmlib falls back to CPU on its
    own if the installed onnxruntime doesn't expose the requested execution
    provider at all.

    A CUDA `device` also needs `onnxruntime.preload_dlls()` called first --
    pip-installed `onnxruntime-gpu` doesn't put the CUDA/cuDNN wheels' shared
    libraries on the loader's default search path, so without this the CUDA
    execution provider silently fails to load and rtmlib falls back to CPU.

    If `out_path` is given the entry is dumped there as a native OpenMMLab
    pickle.
    """
    from rtmlib import Wholebody, YOLOX, RTMPose

    from multidata.device import best_torch_device

    device = device or best_torch_device()
    if device == "cuda":
        ort.preload_dlls(directory="")
    det_device = "cpu" if device == "mps" else device

    model_cfg = Wholebody.MODE[mode]
    det_model = YOLOX(model_cfg["det"], model_input_size=model_cfg["det_input_size"],
                       backend="onnxruntime", device=det_device)
    pose_model = RTMPose(model_cfg["pose"], model_input_size=model_cfg["pose_input_size"],
                         to_openpose=False, backend="onnxruntime", device=device)

    cap = cv2.VideoCapture(str(video_path))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None

    # Per frame, record (keypoints, scores) for everyone detected, in detection order.
    #
    # Deliberately not `Wholebody.__call__`: rtmlib's Wholebody always runs the
    # (expensive) pose network even when the detector finds no one, falling back to the
    # whole frame as a fake bbox. Calling det_model/pose_model separately lets
    # us skip the pose network on empty frames entirely — both for speed (most
    # frames here are an empty room) and correctness (that fallback would
    # otherwise write a phantom whole-frame "person" into slot 0 for every
    # frame with nobody in it).
    frame_dets = []
    timings = {"decode": 0.0, "detect": 0.0, "pose": 0.0}
    last_bboxes = np.empty((0, 4))
    frame_idx = 0
    with tqdm(total=total, unit="frame") as pbar:
        while cap.isOpened():
            t0 = time.perf_counter() if profile else None
            ret, frame = cap.read()
            if not ret:
                break
            t1 = time.perf_counter() if profile else None

            if frame_idx % detect_every == 0:
                last_bboxes = det_model(frame)
            bboxes = last_bboxes
            t2 = time.perf_counter() if profile else None
            if len(bboxes) == 0:
                frame_dets.append((np.empty((0, 0, 0)), np.empty((0, 0))))
            else:
                keypoints, scores = pose_model(frame, bboxes=bboxes)
                frame_dets.append((keypoints, scores))
            t3 = time.perf_counter() if profile else None

            if profile:
                timings["decode"] += t1 - t0
                timings["detect"] += t2 - t1
                timings["pose"] += t3 - t2
            frame_idx += 1
            pbar.update(1)

    if profile:
        spent = sum(timings.values()) or 1e-9
        log.info(
            "profile (%d frames, detect_every=%d): decode=%.1fs (%.0f%%)  "
            "detect[%s]=%.1fs (%.0f%%)  pose[%s]=%.1fs (%.0f%%)",
            len(frame_dets), detect_every,
            timings["decode"], 100 * timings["decode"] / spent,
            det_device, timings["detect"], 100 * timings["detect"] / spent,
            device, timings["pose"], 100 * timings["pose"] / spent,
        )
    cap.release()

    T = len(frame_dets)
    sample = next((kp for kp, _ in frame_dets if len(kp) > 0), None)
    if T == 0 or sample is None:
        raise ValueError(f"No frames, or nobody detected, in {video_path}")
    num_keypoints, num_coords = sample.shape[1:]

    M = max_persons
    video_kpts = np.zeros((M, T, num_keypoints, num_coords), dtype=np.float32)
    video_scores = np.zeros((M, T, num_keypoints), dtype=np.float32)

    for t, (kpts, sc) in enumerate(frame_dets):
        n = min(len(kpts), M)
        if n:
            video_kpts[:n, t] = kpts[:n]
            video_scores[:n, t] = sc[:n]

    entry = {
        "frame_dir": str(video_path).split("/")[-1].replace(".mp4", ""),
        "total_frames": T,
        "img_shape": (height, width),
        "label": 0,
        "keypoint": video_kpts,
        "keypoint_score": video_scores,
    }

    if out_path is not None:
        mmengine.dump(entry, str(out_path))
        log.info("wrote %s  (M=%d, T=%d, V=%d, C=%d)", out_path, M, T, num_keypoints, num_coords)
    return entry


def presence(entry):
    """Per-slot fraction of frames that actually carry a detection (sanity check)."""
    kp = entry["keypoint"]  # (M, T, V, C)
    return (kp != 0).any(axis=(2, 3)).mean(axis=1)


def render_overlay(video_path, entry, out_path, kpt_thr=KPT_THR):
    """Draw slot-colored skeletons + P<slot> labels onto a copy of the video.

    Eyeball check for how badly the missing tracker hurts: scrub and watch one
    label. If P0 stays glued to the same human, the slot is clean; if it jumps
    bodies, it isn't.
    """
    from rtmlib import draw_skeleton

    kp = entry["keypoint"]
    sc = entry["keypoint_score"]
    M, T = kp.shape[:2]

    cap = cv2.VideoCapture(str(video_path))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (width, height))

    t = 0
    while cap.isOpened() and t < T:
        ret, frame = cap.read()
        if not ret:
            break

        for s in range(M):
            visible = sc[s, t] > kpt_thr
            if not visible.any():
                continue  # this person isn't in this frame
            color = SLOT_COLORS[s % len(SLOT_COLORS)]
            # rtmlib's draw_skeleton infers the keypoint count from shape[1] of a
            # *batched* (N, V, C) array, so add a leading instance axis just for
            # drawing. Our stored layout stays (M, T, V, C) untouched.
            frame = draw_skeleton(frame, kp[s, t][None], sc[s, t][None],
                                  kpt_thr=kpt_thr, radius=3, line_width=2)
            xs, ys = kp[s, t, visible, 0], kp[s, t, visible, 1]
            anchor = (int(xs.min()), int(ys.min()) - 8)
            cv2.putText(frame, f"P{s}", anchor, cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, color, 2, cv2.LINE_AA)

        writer.write(frame)
        t += 1

    cap.release()
    writer.release()
    log.info("wrote %s  (%d frames, M=%d)", out_path, t, M)
    return out_path