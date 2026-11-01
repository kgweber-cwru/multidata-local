"""Stage: prosody -- pitch, loudness and voice quality from a case's audio.

Runs in the **md-speech** env (CPU only; needs `praat-parselmouth`). One run
per case, on the case's audio camera (like asr), after diarization.

Two outputs:

1. **Frame features**, `data/prosody/<case_id>/<camera>.npz`, one row per
   Praat pitch frame (10 ms with the default 75 Hz pitch floor):
     time_s        frame centre, seconds from the start of the file
     f0_hz         fundamental frequency (pitch); NaN where unvoiced
     intensity_db  loudness (dB, uncalibrated: compare within a recording)
     hnr_db        harmonics-to-noise ratio, a voice-quality measure; NaN in silence
     f1_hz..f4_hz  formants (vowel quality / vocal tract)
   This is the extraction from the old `acoustics.py` (Praat defaults,
   `notebooks/praat_maker.ipynb` lineage), unchanged except that Praat's
   "-200 dB" silence code for HNR becomes NaN, stored compressed.

2. **Per-speaker summary**, from the diarization turns
   (`data/diarization/<case_id>/<camera>.rttm`), written to the manifest's
   `speaker_prosody` table and `data/prosody/<case_id>/<camera>_speakers.csv`.
   Frames where two diarized speakers overlap are left out of every
   speaker's figures. Pitch spread is in semitones, so voices of different
   register compare fairly. `pitch_bucket` is the rough vocal-register label
   from `speaker_traits` -- for sampling, never a statement about a person.

Diarization speaker labels (SPEAKER_00, ...) are per case: the same label in
two cases is not the same person.
"""
import csv
import json
from pathlib import Path

import numpy as np

from multidata.speaker_traits import pitch_bucket

MIN_VOICED_FRAMES = 10     # fewer voiced frames than this: no pitch figures
HNR_SILENCE = -100.0       # Praat reports -200 dB where it can't measure HNR
FIELDS = ("f0_hz", "intensity_db", "hnr_db", "f1_hz", "f2_hz", "f3_hz", "f4_hz")
SPEAKER_COLUMNS = (
    "speaker", "turns", "speaking_s", "overlap_s", "voiced_s", "median_f0_hz",
    "f0_p10_hz", "f0_p90_hz", "f0_range_st", "f0_sd_st", "mean_intensity_db",
    "median_hnr_db", "pitch_bucket",
)


def frame_features(audio_path):
    """Per-frame pitch, intensity, HNR and F1-F4 (see module docstring).

    Praat objects at their defaults, sampled at the pitch frames' times with
    Praat's own interpolation (`get_value`), as `acoustics.py` did."""
    import parselmouth

    sound = parselmouth.Sound(str(audio_path))
    pitch = sound.to_pitch()
    intensity = sound.to_intensity()
    harmonicity = sound.to_harmonicity()
    formant = sound.to_formant_burg()

    t = np.asarray(pitch.ts(), dtype=np.float64)
    f0 = pitch.selected_array["frequency"].astype(np.float64)
    f0[f0 == 0] = np.nan
    inten = np.array([intensity.get_value(x) for x in t], dtype=np.float64)
    hnr = np.array([harmonicity.get_value(x) for x in t], dtype=np.float64)
    hnr[hnr < HNR_SILENCE] = np.nan
    feats = {"time_s": t, "f0_hz": f0, "intensity_db": inten, "hnr_db": hnr}
    for k in (1, 2, 3, 4):
        feats[f"f{k}_hz"] = np.array([formant.get_value_at_time(k, x) for x in t],
                                     dtype=np.float64)
    feats["params"] = {"praat": parselmouth.PRAAT_VERSION,
                       "parselmouth": parselmouth.__version__,
                       "time_step_s": float(pitch.get_time_step()),
                       "pitch_floor_hz": 75.0, "pitch_ceiling_hz": 600.0,
                       "duration_s": float(sound.duration)}
    return feats


def save(feats, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, time_s=feats["time_s"],
                        **{k: feats[k].astype(np.float32) for k in FIELDS},
                        params=np.array(json.dumps(feats["params"])))
    return out_path


def load(path):
    with np.load(path) as z:
        feats = {"time_s": z["time_s"], **{k: z[k].astype(np.float64) for k in FIELDS}}
        feats["params"] = json.loads(str(z["params"]))
    return feats


def read_rttm(path):
    """[(start_s, end_s, speaker)] from an RTTM file (no pyannote needed)."""
    turns = []
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            start, dur = float(parts[3]), float(parts[4])
            turns.append((start, start + dur, parts[7]))
    return turns


def speaker_summary(feats, turns):
    """One dict per diarized speaker (keys = SPEAKER_COLUMNS).

    Every speaker in `turns` gets a row; pitch figures are None when the
    speaker has fewer than MIN_VOICED_FRAMES voiced frames outside overlaps.
    """
    t = feats["time_s"]
    step = float(np.median(np.diff(t))) if len(t) > 1 else 0.01
    speakers = sorted({s for _, _, s in turns})
    masks = {s: np.zeros(len(t), bool) for s in speakers}
    for start, end, s in turns:
        masks[s] |= (t >= start) & (t < end)
    n_active = np.sum([m for m in masks.values()], axis=0) if speakers else np.zeros(len(t))
    overlap = n_active > 1

    rows = []
    for s in speakers:
        mine = masks[s] & ~overlap
        f0 = feats["f0_hz"][mine]
        voiced = f0[~np.isnan(f0)]
        row = {"speaker": s,
               "turns": sum(1 for *_, x in turns if x == s),
               "speaking_s": round(sum(e - b for b, e, x in turns if x == s), 2),
               "overlap_s": round(float((masks[s] & overlap).sum() * step), 2),
               "voiced_s": round(float(len(voiced) * step), 2)}
        if len(voiced) >= MIN_VOICED_FRAMES:
            med = float(np.median(voiced))
            p10, p90 = (float(x) for x in np.percentile(voiced, [10, 90]))
            st = 12 * np.log2(voiced / med)
            row.update(median_f0_hz=round(med, 2), f0_p10_hz=round(p10, 2),
                       f0_p90_hz=round(p90, 2), f0_range_st=round(12 * np.log2(p90 / p10), 2),
                       f0_sd_st=round(float(np.std(st)), 2),
                       mean_intensity_db=round(float(np.nanmean(feats["intensity_db"][mine][~np.isnan(f0)])), 2),
                       median_hnr_db=round(float(np.nanmedian(feats["hnr_db"][mine][~np.isnan(f0)])), 2),
                       pitch_bucket=pitch_bucket(med))
        else:
            row.update({k: None for k in SPEAKER_COLUMNS if k not in row})
        rows.append(row)
    return rows


def write_speakers_csv(rows, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SPEAKER_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return out_path
