"""Stage: prosody -- pitch, loudness and voice quality from a recording's audio.

Runs in the **md-speech** env (CPU only; needs `praat-parselmouth`). One run
per recording, on the recording's audio camera (like asr), after diarization.

Two outputs:

1. **Frame features**, `data/prosody/<recording_id>/<camera>.npz`, one row per
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
   (`data/diarization/<recording_id>/<camera>.rttm`), written to the manifest's
   `speaker_prosody` table and `data/prosody/<recording_id>/<camera>_speakers.csv`.
   Frames where two diarized speakers overlap are left out of every
   speaker's figures. Pitch spread is in semitones, so voices of different
   register compare fairly. `pitch_bucket` is the rough vocal-register label
   from `speaker_traits` -- for sampling, never a statement about a person.

3. **Praat files** beside the features: `<camera>.PitchTier`,
   `.IntensityTier` and a `.TextGrid` of speaker turns (`export_praat`).
   `voiced_stretches()` feeds the ELAN pitch tier (`elan.PITCH_TIER`).

Diarization speaker labels (SPEAKER_00, ...) are per recording: the same label in
two recordings is not the same person. Field-by-field reference:
docs/data_formats.md.
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


# --- Praat and ELAN exports ------------------------------------------------
#
# Written next to the frame features by the prosody stage, for opening the
# recording in Praat together with its WAV (data/audio/<recording_id>/<camera>.wav):
#   <camera>.PitchTier      pitch (Hz) at every voiced frame
#   <camera>.IntensityTier  loudness (dB) at every frame
#   <camera>.TextGrid       one interval tier per diarized speaker (who spoke when)
# All three are Praat's own text format, on the same clock as the WAV. They
# depend on the audio and the diarization only, never on the transcript.

def _q(s):
    return '"' + str(s).replace('"', '""') + '"'


def _tier_header(cls, xmax, n):
    return (f'File type = "ooTextFile"\nObject class = "{cls}"\n\n'
            f"xmin = 0 \nxmax = {xmax!r} \npoints: size = {n} \n")


def write_point_tier(cls, times, values, xmax, out_path):
    """A Praat PitchTier / IntensityTier text file (NaN values skipped)."""
    keep = ~np.isnan(values)
    t, v = np.asarray(times)[keep], np.asarray(values)[keep]
    lines = [_tier_header(cls, float(xmax), len(t))]
    for i, (a, b) in enumerate(zip(t, v), 1):
        lines.append(f"points [{i}]:\n    number = {float(a)!r} \n    value = {float(b)!r} \n")
    Path(out_path).write_text("".join(lines))
    return out_path


def write_textgrid(turns, xmax, out_path):
    """A Praat TextGrid with one interval tier per speaker: the speaker's
    name over each of their turns (overlapping turns merged), empty
    intervals between, covering 0..xmax as Praat requires."""
    speakers = sorted({s for _, _, s in turns})
    out = [f'File type = "ooTextFile"\nObject class = "TextGrid"\n\n'
           f"xmin = 0 \nxmax = {float(xmax)!r} \ntiers? <exists> \nsize = {len(speakers)} \nitem []: \n"]
    for k, spk in enumerate(speakers, 1):
        merged = []
        for b, e, _ in sorted(t for t in turns if t[2] == spk):
            b, e = max(0.0, b), min(float(xmax), e)
            if e <= b:
                continue
            if merged and b <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([b, e])
        intervals, cur = [], 0.0
        for b, e in merged:
            if b > cur:
                intervals.append((cur, b, ""))
            intervals.append((b, e, spk))
            cur = e
        if cur < xmax:
            intervals.append((cur, float(xmax), ""))
        out.append(f"    item [{k}]:\n        class = \"IntervalTier\" \n        name = {_q(spk)} \n"
                   f"        xmin = 0 \n        xmax = {float(xmax)!r} \n"
                   f"        intervals: size = {len(intervals)} \n")
        for i, (b, e, text) in enumerate(intervals, 1):
            out.append(f"        intervals [{i}]:\n            xmin = {b!r} \n"
                       f"            xmax = {e!r} \n            text = {_q(text)} \n")
    Path(out_path).write_text("".join(out))
    return out_path


def export_praat(feats, turns, base_path):
    """Write <base>.PitchTier, <base>.IntensityTier and <base>.TextGrid."""
    base = Path(base_path)
    xmax = feats["params"]["duration_s"]
    return [write_point_tier("PitchTier", feats["time_s"], feats["f0_hz"], xmax,
                             base.with_suffix(".PitchTier")),
            write_point_tier("IntensityTier", feats["time_s"], feats["intensity_db"], xmax,
                             base.with_suffix(".IntensityTier")),
            write_textgrid(turns, xmax, base.with_suffix(".TextGrid"))]


def voiced_stretches(feats, min_s=0.1, max_gap_s=0.03, max_jump_st=3.0):
    """[(start_s, end_s, median_f0_hz)] for each stretch of continuous voicing
    (gaps up to `max_gap_s` bridged, stretches shorter than `min_s` dropped).
    A stretch also ends where pitch jumps more than `max_jump_st` semitones
    from one frame to the next: intonation doesn't move that fast in 10 ms,
    but a change of voice (one speaker cutting in on another) does.
    This is what the ELAN pitch tier shows: one value per stretch, built from
    the audio alone, so re-running ASR never changes it."""
    t, f0 = feats["time_s"], feats["f0_hz"]
    if len(t) < 2:
        return []
    step = float(np.median(np.diff(t)))
    voiced = ~np.isnan(f0)
    out, start, last = [], None, None
    for i in np.flatnonzero(voiced):
        if start is not None and (t[i] - t[last] > max_gap_s + step
                                  or abs(12 * np.log2(f0[i] / f0[last])) > max_jump_st):
            out.append((start, last))
            start = None
        if start is None:
            start = i
        last = i
    if start is not None:
        out.append((start, last))
    res = []
    for a, b in out:
        t0, t1 = float(t[a] - step / 2), float(t[b] + step / 2)
        if t1 - t0 >= min_s:
            res.append((t0, t1, float(np.nanmedian(f0[a:b + 1]))))
    return res
