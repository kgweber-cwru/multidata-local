"""Rough per-speaker pitch summary, for annotation-sample stratification —
NOT a demographic determination about any real person.

Median fundamental frequency (F0) is the standard acoustic proxy for vocal
register: adult voices cluster roughly 85-180 Hz "male-typical" vs.
165-255 Hz "female-typical", with a real overlap band around 145-185 Hz
(naturally low-pitched women, naturally high-pitched men, atypical voices
generally). This exists purely to help pick a diverse sample of vocal
registers for gold annotation (docs/gold_annotation_guide.md §2) — treat the
bucket labels as a rough stratification knob, never as a fact recorded about
a real person.

Runs in **md-speech** (needs `praat-parselmouth`, already a dependency via
`acoustics.py`).
"""

# Bucket edges in Hz, chosen from the typical adult clustering above.
LOWER_TYPICAL_MAX = 145.0
HIGHER_TYPICAL_MIN = 185.0


def speaker_pitch_stats(audio_path, annotation, min_voiced_frames=10):
    """Median F0 (Hz) per speaker, restricted to that speaker's diarization
    turns.

    `annotation` is a pyannote Annotation (`diarize.diarize()`'s return value,
    or `diarize.load_rttm()` reloading a cached one). Speakers with fewer than
    `min_voiced_frames` voiced pitch frames in their turns are dropped rather
    than reported on a handful of noisy samples.

    Returns {speaker: {"median_hz": float, "n_voiced_frames": int}}.
    """
    import numpy as np
    import parselmouth

    sound = parselmouth.Sound(str(audio_path))
    pitch = sound.to_pitch()
    times = pitch.ts()
    freqs = pitch.selected_array["frequency"]  # 0.0 == unvoiced, not NaN

    stats = {}
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        mask = (times >= segment.start) & (times < segment.end) & (freqs > 0)
        stats.setdefault(speaker, []).extend(freqs[mask].tolist())

    return {
        speaker: {"median_hz": float(np.median(vals)), "n_voiced_frames": len(vals)}
        for speaker, vals in stats.items()
        if len(vals) >= min_voiced_frames
    }


def pitch_bucket(median_hz):
    """"lower" / "ambiguous" / "higher" vocal-register bucket for one median
    F0 -- a rough stratification label, not a sex/gender determination."""
    if median_hz < LOWER_TYPICAL_MAX:
        return "lower"
    if median_hz > HIGHER_TYPICAL_MIN:
        return "higher"
    return "ambiguous"


def speaker_register_summary(audio_path, annotation, min_voiced_frames=10):
    """`speaker_pitch_stats` plus a `bucket` label per speaker -- the
    convenience entry point for sampling decisions."""
    stats = speaker_pitch_stats(audio_path, annotation, min_voiced_frames)
    for speaker, s in stats.items():
        s["bucket"] = pitch_bucket(s["median_hz"])
    return stats
