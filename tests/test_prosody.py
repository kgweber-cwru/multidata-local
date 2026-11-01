"""multidata.prosody on synthetic voices with known pitch (md-speech)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
parselmouth = pytest.importorskip("parselmouth")
from multidata import manifest, prosody  # noqa: E402

SR = 16000


def _voice(f0, seconds, amp=0.3):
    """A buzzy vowel-like tone: harmonics of f0 with falling strength."""
    t = np.arange(int(seconds * SR)) / SR
    return sum(amp / k * np.sin(2 * np.pi * k * f0 * t) for k in range(1, 12))


@pytest.fixture(scope="module")
def two_speakers(tmp_path_factory):
    """0-2 s speaker A at 120 Hz, 2-4 s speaker B at 220 Hz, 4-5 s silence."""
    sig = np.concatenate([_voice(120, 2), _voice(220, 2), np.zeros(SR)])
    path = tmp_path_factory.mktemp("p") / "case.wav"
    parselmouth.Sound(sig, sampling_frequency=SR).save(str(path), "WAV")
    return path, prosody.frame_features(path)


def test_frame_features_shape_and_pitch(two_speakers):
    _, f = two_speakers
    assert set(prosody.FIELDS) <= set(f) and len(f["time_s"]) > 300
    a = f["f0_hz"][(f["time_s"] > 0.3) & (f["time_s"] < 1.7)]
    b = f["f0_hz"][(f["time_s"] > 2.3) & (f["time_s"] < 3.7)]
    assert abs(np.nanmedian(a) - 120) < 2 and abs(np.nanmedian(b) - 220) < 2
    silent = f["time_s"] > 4.3
    assert np.isnan(f["f0_hz"][silent]).all() and np.isnan(f["hnr_db"][silent]).all()


def test_speaker_summary(two_speakers):
    _, f = two_speakers
    rows = {r["speaker"]: r for r in prosody.speaker_summary(
        f, [(0.0, 2.0, "A"), (2.0, 4.0, "B"), (4.0, 5.0, "C")])}
    assert abs(rows["A"]["median_f0_hz"] - 120) < 2 and rows["A"]["pitch_bucket"] == "lower"
    assert abs(rows["B"]["median_f0_hz"] - 220) < 2 and rows["B"]["pitch_bucket"] == "higher"
    assert rows["A"]["f0_range_st"] < 1          # a steady tone barely moves
    assert rows["C"]["median_f0_hz"] is None     # silence: row kept, no pitch figures
    assert rows["A"]["speaking_s"] == 2.0 and rows["A"]["overlap_s"] == 0


def test_overlap_is_left_out(two_speakers):
    _, f = two_speakers
    rows = {r["speaker"]: r for r in prosody.speaker_summary(
        f, [(0.0, 3.0, "A"), (2.0, 4.0, "B")])}   # 2-3 s overlaps
    assert abs(rows["A"]["median_f0_hz"] - 120) < 2   # B's 220 Hz not credited to A
    assert abs(rows["A"]["overlap_s"] - 1.0) < 0.05


def test_rttm_and_save_round_trip(two_speakers, tmp_path):
    _, f = two_speakers
    rttm = tmp_path / "x.rttm"
    rttm.write_text("SPEAKER x 1 0.500 1.250 <NA> <NA> SPEAKER_00 <NA> <NA>\n"
                    "SPEAKER x 1 2.000 0.500 <NA> <NA> SPEAKER_01 <NA> <NA>\n")
    assert prosody.read_rttm(rttm) == [(0.5, 1.75, "SPEAKER_00"), (2.0, 2.5, "SPEAKER_01")]
    prosody.save(f, tmp_path / "p.npz")
    g = prosody.load(tmp_path / "p.npz")
    assert np.allclose(g["f0_hz"], f["f0_hz"], equal_nan=True, rtol=1e-6)
    assert g["params"]["time_step_s"] == f["params"]["time_step_s"]


def test_manifest_rows_and_export(two_speakers, tmp_path):
    _, f = two_speakers
    db = tmp_path / "m.sqlite"
    rows = prosody.speaker_summary(f, [(0.0, 2.0, "A"), (2.0, 4.0, "B")])
    manifest.set_speaker_prosody("c1", "20", rows, db)
    manifest.set_speaker_prosody("c1", "20", rows, db)       # re-run replaces
    assert manifest.export_speaker_prosody(tmp_path / "all.csv", db) == 2
    assert "median_f0_hz" in (tmp_path / "all.csv").read_text().splitlines()[0]
