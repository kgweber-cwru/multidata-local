"""Rule-based pose features (promoted from `notebooks/mmpose.ipynb` cells 7-14).

Operates on one slot of a pose entry: `entry["keypoint"][s]`, shape (T, V, C)
in COCO-17 order (rtmlib `Body`). Runs in the **md-pose** env.

Exploratory, not validated against gold annotations yet — thresholds below are
the notebook's eyeballed starting values, not tuned constants.
"""
import numpy as np

# COCO-17 indices used here
NOSE, L_SHOULDER, R_SHOULDER, L_WRIST, R_WRIST = 0, 5, 6, 9, 10


def torso_facing_angle(keypoints):
    """Horizontal angle of the upper torso for a single frame.

    keypoints: (V, C) -> [x, y, (confidence)]
    Returns (angle_deg, forward_lean_pixels). Lean is the nose's vertical offset
    from the shoulder midpoint — a proxy for forward tilt, in pixels.
    """
    left_shoulder = keypoints[L_SHOULDER][:2]
    right_shoulder = keypoints[R_SHOULDER][:2]

    # Angle of the shoulder-to-shoulder vector relative to the image horizon
    shoulder_vector = right_shoulder - left_shoulder
    angle_deg = np.degrees(np.arctan2(shoulder_vector[1], shoulder_vector[0]))

    shoulder_mid = (left_shoulder + right_shoulder) / 2.0
    forward_lean_pixels = keypoints[NOSE][1] - shoulder_mid[1]

    return angle_deg, forward_lean_pixels


def facing_angle_series(kpts_series, smooth=5):
    """Per-frame torso angle over (T, V, C), moving-average smoothed."""
    angles = [torso_facing_angle(frame)[0] for frame in kpts_series]
    return np.convolve(angles, np.ones(smooth) / smooth, mode="valid")


def looking_away(smoothed_angles, threshold_deg=20.0):
    """Boolean mask of frames turned more than `threshold_deg` off-center."""
    return np.abs(smoothed_angles) > threshold_deg


def hand_kinematics(kpts_series, fps=30):
    """Frame-by-frame max wrist velocity in pixels/second over (T, V, C)."""
    r_wrists = kpts_series[:, R_WRIST, :2]
    l_wrists = kpts_series[:, L_WRIST, :2]

    # Euclidean frame-to-frame displacement -> pixels/second
    r_velocity = np.linalg.norm(np.diff(r_wrists, axis=0), axis=1) * fps
    l_velocity = np.linalg.norm(np.diff(l_wrists, axis=0), axis=1) * fps

    return np.maximum(r_velocity, l_velocity)


def gesturing(hand_velocity, threshold_px_s=150.0):
    """Boolean mask of frames with active gesturing / rapid hand movement."""
    return hand_velocity > threshold_px_s
