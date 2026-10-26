import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
pytest.importorskip("cv2")  # pose.py imports the md-pose stack at module level
pytest.importorskip("mmengine")
from multidata import pose  # noqa: E402


def _entry(M=4, T=50, V=133, seed=0):
    rng = np.random.default_rng(seed)
    kp = np.zeros((M, T, V, 2), dtype=np.float32)
    sc = np.zeros((M, T, V), dtype=np.float32)
    kp[:2] = rng.uniform(0, [1280, 720], size=(2, T, V, 2))  # two people, two empty slots
    sc[:2] = rng.uniform(0, 1, size=(2, T, V))
    return {"frame_dir": "20260216-132904-v307848-13", "total_frames": T,
            "img_shape": (720, 1280), "label": 0, "keypoint": kp, "keypoint_score": sc}


class TestSaveLoad:
    def test_round_trip_metadata_and_dtype(self, tmp_path):
        e = _entry()
        pose.save(e, tmp_path / "13.npz")
        got = pose.load(tmp_path / "13.npz")
        for key in ("frame_dir", "total_frames", "img_shape", "label"):
            assert got[key] == e[key]
        assert got["keypoint"].dtype == np.float32
        assert got["keypoint_score"].dtype == np.float32

    def test_float16_error_within_half_pixel(self, tmp_path):
        e = _entry()
        pose.save(e, tmp_path / "13.npz")
        got = pose.load(tmp_path / "13.npz")
        assert np.abs(got["keypoint"] - e["keypoint"]).max() <= 0.5
        assert np.abs(got["keypoint_score"] - e["keypoint_score"]).max() <= 1e-3

    def test_empty_slots_stay_exactly_zero(self, tmp_path):
        e = _entry()
        pose.save(e, tmp_path / "13.npz")
        got = pose.load(tmp_path / "13.npz")
        assert not got["keypoint"][2:].any()

    def test_much_smaller_than_pickle(self, tmp_path):
        import mmengine
        e = _entry(T=500)
        mmengine.dump(e, str(tmp_path / "13.pkl"))
        pose.save(e, tmp_path / "13.npz")
        assert (tmp_path / "13.npz").stat().st_size < (tmp_path / "13.pkl").stat().st_size / 3

    def test_loads_legacy_pickle(self, tmp_path):
        import mmengine
        e = _entry()
        mmengine.dump(e, str(tmp_path / "13.pkl"))
        got = pose.load(tmp_path / "13.pkl")
        assert np.array_equal(got["keypoint"], e["keypoint"])
