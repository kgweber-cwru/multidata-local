"""pose.save()/load() round trip for HALPE_26 entries (md-pose env)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
pytest.importorskip("cv2")  # pose.py imports the md-pose stack at module level
pytest.importorskip("onnxruntime")
from multidata import pose  # noqa: E402


def _entry(M=8, T=50, seed=0):
    V = len(pose.KEYPOINTS)
    rng = np.random.default_rng(seed)
    kp = np.zeros((M, T, V, 2), dtype=np.float32)
    sc = np.zeros((M, T, V), dtype=np.float32)
    bb = np.zeros((M, T, 4), dtype=np.float32)
    kp[:2] = rng.uniform(0, [1280, 720], size=(2, T, V, 2))  # two people, rest empty
    sc[:2] = rng.uniform(0, 1, size=(2, T, V))
    bb[:2] = rng.uniform(0, 1280, size=(2, T, 4))
    return {"frame_dir": "20260216-132904-v307848-13", "total_frames": T,
            "img_shape": (720, 1280), "keypoint_set": pose.KEYPOINT_SET,
            "keypoint": kp, "keypoint_score": sc, "bbox": bb,
            "frame_ms": np.arange(T) * 1000 / 30 + 1e-3,
            "models": "yolox_m + rtmpose-m halpe26", "mode": "balanced",
            "detect_every": 1, "max_persons": M, "rtmlib_version": "0.0.16"}


def test_halpe26_layout():
    assert len(pose.KEYPOINTS) == 26
    assert pose.KEYPOINTS[:5] == ["nose", "left_eye", "right_eye", "left_ear", "right_ear"]
    assert pose.KEYPOINTS[16] == "right_ankle"      # first 17 = COCO body order
    assert pose.KEYPOINTS[17:20] == ["head", "neck", "hip"]


class TestSaveLoad:
    def test_round_trip_metadata_and_dtype(self, tmp_path):
        e = _entry()
        pose.save(e, tmp_path / "13.npz")
        got = pose.load(tmp_path / "13.npz")
        for key in pose._META:
            assert got[key] == e[key], key
        for key in pose._ARRAYS:
            assert got[key].dtype == np.float32
        assert got["frame_ms"].dtype == np.float64

    def test_float16_error_within_half_pixel(self, tmp_path):
        e = _entry()
        pose.save(e, tmp_path / "13.npz")
        got = pose.load(tmp_path / "13.npz")
        assert np.abs(got["keypoint"] - e["keypoint"]).max() <= 0.5
        assert np.abs(got["keypoint_score"] - e["keypoint_score"]).max() <= 1e-3

    def test_frame_times_exact(self, tmp_path):
        e = _entry()
        pose.save(e, tmp_path / "13.npz")
        assert np.array_equal(pose.load(tmp_path / "13.npz")["frame_ms"], e["frame_ms"])

    def test_empty_slots_stay_exactly_zero(self, tmp_path):
        e = _entry()
        pose.save(e, tmp_path / "13.npz")
        got = pose.load(tmp_path / "13.npz")
        assert not got["keypoint"][2:].any() and not got["bbox"][2:].any()

    def test_presence(self, tmp_path):
        p = pose.presence(_entry())
        assert p[0] > 0.9 and p[2] == 0
