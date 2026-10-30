"""multidata.views on synthetic footage, and the manifest's view tables."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
cv2 = pytest.importorskip("cv2")
from multidata import manifest, views  # noqa: E402


def _room(seed=0, size=(400, 260)):
    """A textured 'room': blurred noise plus hard-edged boxes (corners for ORB)."""
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.integers(0, 255, size[::-1], dtype=np.uint8), (5, 5), 0)
    for _ in range(60):
        x, y = rng.integers(0, size[0] - 30), rng.integers(0, size[1] - 30)
        w, h = rng.integers(8, 30, 2)
        cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), int(rng.integers(0, 255)), -1)
    return img


def _view(room, dx, dy=0):
    """The camera's W x H window onto the room, panned by (dx, dy)."""
    return room[20 + dy:20 + dy + views.H, 20 + dx:20 + dx + views.W].copy()


def test_motion_measures_a_pan():
    room = _room()
    p = views.motion(views.features(_view(room, 0)), views.features(_view(room, 20)))
    assert p is not None
    assert abs(abs(p[0]) - 20) < 1.5 and abs(p[1]) < 1.5 and abs(p[2] - 1) < 0.01


def test_segment_finds_one_move():
    room = _room()
    frames = [_view(room, 0)] * 12 + [_view(room, 25)] * 12
    bounds = views.segment([views.features(f) for f in frames])
    assert bounds == [0, 12, 24]


def test_segment_ignores_a_steady_camera():
    room = _room()
    frames = [_view(room, 0)] * 20
    assert views.segment([views.features(f) for f in frames]) == [0, 20]


def test_library_ids_are_stable_across_reload(tmp_path):
    room = _room()
    a = np.stack([_view(room, 0)] * views.N_SAMPLES)
    b = np.stack([_view(room, 40)] * views.N_SAMPLES)
    lib = views.Library(tmp_path, "14")
    assert lib.match([views.features(f) for f in a]) == (None, None)
    assert lib.add(a) == "v1"
    assert lib.add(b) == "v2"
    again = views.Library(tmp_path, "14")                # fresh load from disk
    vid, p = again.match([views.features(_view(room, 1))] * 3)   # preset recalled 1 px off
    assert vid == "v1"
    assert again.match([views.features(_view(room, 41))] * 3)[0] == "v2"
    assert (tmp_path / "14" / "v1.png").exists()


def test_view_tables_and_preset_rule(tmp_path):
    db = tmp_path / "m.sqlite"
    manifest.add_camera_view("14", "v1", "c1", "data/views/library/14/v1.png", db)
    manifest.add_camera_view("14", "v2", "c1", "data/views/library/14/v2.png", db)
    manifest.set_video_views("c1", "14", {"v1": (600.0, 1), "v2": (40.0, 1)}, db)
    manifest.set_video_views("c2", "14", {"v1": (300.0, 2)}, db)
    manifest.set_video_views("c2", "14", {"v1": (310.0, 2)}, db)   # re-run replaces
    manifest.set_view_label("14", "v1", "exam table", "confirmed", db)
    s = {r["view_id"]: r for r in manifest.view_summary(db)}
    assert s["v1"]["seconds"] == 910.0 and s["v1"]["cases"] == 2 and s["v1"]["is_preset"]
    assert s["v1"]["label"] == "exam table"
    assert not s["v2"]["is_preset"]
    with pytest.raises(KeyError):
        manifest.set_view_label("14", "v9", "sink", path=db)
