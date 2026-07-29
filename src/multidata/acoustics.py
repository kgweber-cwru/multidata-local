"""Acoustic feature extraction via Praat/parselmouth.

Promoted from `notebooks/praat_maker.ipynb` cells 0-1 of the old multidata repo
(the plotting cells stayed in the notebook — they're exploratory, not pipeline).
Runs in the **md-speech** env; needs `praat-parselmouth`.

Frame grid follows Praat's pitch object time steps. Not wired into `run_stage.py`
yet — it has no manifest status column (see doc §10).
"""
import csv

import numpy as np
import parselmouth

HEADERS = [
    "Frame", "Start", "End", "Pitches", "Intensities",
    "Harmonicities", "Formant 1", "Formant 2", "Formant 3", "Formant 4",
]


def extract_features(audio_path, out_csv):
    """Per-frame pitch, intensity, harmonicity and F1-F4 -> CSV."""
    sound = parselmouth.Sound(str(audio_path))

    pitch = sound.to_pitch()
    intensity = sound.to_intensity()
    harmonicity = sound.to_harmonicity()
    formant = sound.to_formant_burg()

    time_steps = pitch.ts()
    frame_duration = pitch.get_time_step()

    with open(out_csv, mode="w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(HEADERS)

        for i, t in enumerate(time_steps):
            start_time = max(0, t - frame_duration / 2.0)
            end_time = t + frame_duration / 2.0

            p = pitch.get_value_at_time(t)
            inte = intensity.get_value(t)
            harm = harmonicity.get_value(t)

            f1 = formant.get_value_at_time(1, t)
            f2 = formant.get_value_at_time(2, t)
            f3 = formant.get_value_at_time(3, t)
            f4 = formant.get_value_at_time(4, t)

            # Undefined pitch/harmonicity -> empty cell rather than the string "nan"
            p = "" if np.isnan(p) else p
            harm = "" if np.isnan(harm) else harm

            writer.writerow([i + 1, start_time, end_time, p, inte, harm, f1, f2, f3, f4])

    print(f"Data successfully exported to {out_csv}")
    return out_csv
