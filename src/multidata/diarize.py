"""Stage: diarize — pyannote speaker diarization, run directly (doc §6).

Standalone from `asr.py` on purpose, even though the `whisperx` engine also
diarizes internally:

1. **DER needs RTTM.** `pyannote.metrics` scores diarization against a gold RTTM
   (doc §8b); this stage is what produces the hypothesis side of that.
2. It lets you pair diarization with an ASR engine that has none (`faster_whisper`).

Gated model — accept the terms for `pyannote/speaker-diarization-3.1` on
huggingface.co and put a token in `.env` as `HF_TOKEN` first (see
`multidata.hf_auth`), or `from_pretrained` returns None.
"""
from pathlib import Path

from multidata.hf_auth import hf_token

MODEL = "pyannote/speaker-diarization-3.1"


def diarize(audio_path, out_rttm=None, num_speakers=None,
            min_speakers=None, max_speakers=None, device="cpu"):
    """Run pyannote on one WAV -> pyannote Annotation, optionally written as RTTM.

    Speaker-count hints are a benchmark axis, not a constant (doc §8c): our
    cases are typically 3-4 people, but pinning it can help or hurt.
    """
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(MODEL, token=hf_token())
    if pipeline is None:
        raise RuntimeError(
            f"{MODEL} returned None — accept the model terms on huggingface.co "
            "for this token's account."
        )
    pipeline.to(torch.device(device))

    hints = {k: v for k, v in {
        "num_speakers": num_speakers,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
    }.items() if v is not None}

    annotation = pipeline(str(audio_path), **hints)

    if out_rttm is not None:
        out_rttm = Path(out_rttm)
        out_rttm.parent.mkdir(parents=True, exist_ok=True)
        with open(out_rttm, "w") as f:
            annotation.write_rttm(f)
        print(f"Wrote {out_rttm}  ({len(annotation.labels())} speakers)")

    return annotation


def turns(annotation):
    """Annotation -> [{start, end, speaker}], sorted by start time."""
    return [
        {"start": segment.start, "end": segment.end, "speaker": label}
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]
