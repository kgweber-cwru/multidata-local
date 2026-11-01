"""Frame-level Praat features as CSV -- kept for older notebooks.

The pipeline now uses `multidata.prosody` (the `prosody` stage), which does
the same extraction and stores it compactly with a per-speaker summary. This
wrapper writes the original CSV layout from it. md-speech env.
"""
import csv

import numpy as np

from multidata import prosody

HEADERS = [
    "Frame", "Start", "End", "Pitches", "Intensities",
    "Harmonicities", "Formant 1", "Formant 2", "Formant 3", "Formant 4",
]


def extract_features(audio_path, out_csv):
    """Per-frame pitch, intensity, harmonicity and F1-F4 -> CSV."""
    f = prosody.frame_features(audio_path)
    half = f["params"]["time_step_s"] / 2.0
    blank = lambda x: "" if np.isnan(x) else x  # noqa: E731
    with open(out_csv, mode="w", newline="") as csv_file:
        w = csv.writer(csv_file)
        w.writerow(HEADERS)
        for i, t in enumerate(f["time_s"]):
            w.writerow([i + 1, max(0, t - half), t + half, blank(f["f0_hz"][i]),
                        f["intensity_db"][i], blank(f["hnr_db"][i]),
                        f["f1_hz"][i], f["f2_hz"][i], f["f3_hz"][i], f["f4_hz"][i]])
    return out_csv
