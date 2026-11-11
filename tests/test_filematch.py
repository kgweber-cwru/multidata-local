"""Large rooms are recognized, and still match (the pilot scope is applied
per stage in run_stage.py, not at ingest)."""
from types import SimpleNamespace

import pytest

from multidata.filematch import is_large_room, match_recording


@pytest.mark.parametrize("room,large", [
    ("00 LA", True), ("00 LB", True), ("'00 LA'", True), ("00 la", True),
    ("06", False), ("'09'", False), ("14", False),
])
def test_is_large_room(room, large):
    assert is_large_room(room) is large


def _recording(recording_id, room, start):
    return SimpleNamespace(recording_id=recording_id, room_number=room, recording_start_time=start)


def test_large_room_video_still_matches():
    recordings = [_recording("1", "00 LA", "2025-05-15 16:09")]
    assert match_recording("2025-05-15", "16:09:38", "15", recordings, {"15": "00 LA"}) == "1"


def test_small_room_still_matches():
    recordings = [_recording("2", "06", "2026-02-16 12:45")]
    assert match_recording("2026-02-16", "12:45:05", "14", recordings, {"14": "6"}) == "2"
