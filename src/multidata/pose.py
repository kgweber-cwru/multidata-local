"""Stage: pose — rtmlib RTMPose extraction (pipeline doc §9).

Promoted from `notebooks/mmpose.ipynb` cells 2-4 of the old multidata repo.
Runs in the **md-pose** env.

Tracker is OFF, deliberately: slot `s` is just the s-th detection in that frame
and is NOT the same person across frames. Stable identity is deferred work, not
a pipeline blocker (doc §9).

Output per camera: `data/pose/<case_id>/<camera>.pkl` holding one entry dict
with `keypoint` (M, T, V, C) and `keypoint_score` (M, T, V), both float32.
"""
import cv2
import mmengine
import numpy as np

MAX_PERSONS = 4  # fixed M slots; frames with fewer people leave slots at zero
KPT_THR = 0.3

# One fixed BGR color per slot, so "P0" is the same hue across the whole video.
SLOT_COLORS = [(0, 0, 255), (0, 200, 0), (255, 128, 0), (255, 0, 255),
               (0, 255, 255), (255, 255, 0)]


def extract(video_path, out_path=None, max_persons=MAX_PERSONS,
            mode="balanced", device="cpu"):
    """Run RTMPose over every frame of one video -> MMAction2-style entry dict.

    Detection on every frame; the pose network only runs on frames where
    detection found someone. No identity across frames. If `out_path` is
    given the entry is dumped there as a native OpenMMLab pickle.
    """
    from rtmlib import Body

    body = Body(mode=mode, to_openpose=False, backend="onnxruntime", device=device)

    cap = cv2.VideoCapture(str(video_path))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Per frame, record (keypoints, scores) for everyone detected, in detection order.
    #
    # Deliberately not `body(frame)`: rtmlib's Body.__call__ always runs the
    # (expensive) pose network even when the detector finds no one, falling
    # back to the whole frame as a fake bbox. Calling det_model/pose_model
    # separately lets us skip the pose network on empty frames entirely —
    # both for speed (most frames here are an empty room) and correctness
    # (that fallback would otherwise write a phantom whole-frame "person"
    # into slot 0 for every frame with nobody in it).
    frame_dets = []
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        current_frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        if current_frame_idx % 30 == 0:
            print(f"Processing frame {current_frame_idx}")

        bboxes = body.det_model(frame)
        if len(bboxes) == 0:
            frame_dets.append((np.empty((0, 0, 0)), np.empty((0, 0))))
            continue
        keypoints, scores = body.pose_model(frame, bboxes=bboxes)  # (N, V, C), (N, V)
        frame_dets.append((keypoints, scores))
    cap.release()

    T = len(frame_dets)
    # Infer keypoint count (V) and channels (C) from the first real detection
    sample = next((kp for kp, _ in frame_dets if len(kp) > 0), None)
    if T == 0 or sample is None:
        raise ValueError(f"No frames, or nobody detected, in {video_path}")
    num_keypoints, num_coords = sample.shape[1:]  # (V, C), e.g. (17, 2) for Body

    M = max_persons
    video_kpts = np.zeros((M, T, num_keypoints, num_coords), dtype=np.float32)
    video_scores = np.zeros((M, T, num_keypoints), dtype=np.float32)

    for t, (kpts, sc) in enumerate(frame_dets):
        n = min(len(kpts), M)  # keep at most M detections this frame
        if n:
            video_kpts[:n, t] = kpts[:n]
            video_scores[:n, t] = sc[:n]

    entry = {
        "frame_dir": str(video_path).split("/")[-1].replace(".mp4", ""),
        "total_frames": T,
        "img_shape": (height, width),
        "label": 0,
        "keypoint": video_kpts,          # (M, T, V, C)
        "keypoint_score": video_scores,  # (M, T, V)
    }

    if out_path is not None:
        mmengine.dump(entry, str(out_path))
        print(f"Wrote {out_path}  (M={M}, T={T}, V={num_keypoints}, C={num_coords})")
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
    print(f"Wrote {out_path}  ({t} frames, M={M})")
    return out_path
