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
import logging
from pathlib import Path

from multidata.hf_auth import hf_token

log = logging.getLogger(__name__)

MODEL = "pyannote/speaker-diarization-3.1"


def diarize(audio_path, out_rttm=None, num_speakers=None,
            min_speakers=None, max_speakers=None, device=None):
    """Run pyannote on one WAV -> pyannote Annotation, optionally written as RTTM.

    Speaker-count hints are a benchmark axis, not a constant (doc §8c): our
    cases are typically 3-4 people, but pinning it can help or hurt.

    `device` defaults to the best available (`multidata.device.best_torch_device`
    — mps > cuda > cpu) — confirmed empirically ~7x faster under CoreML/MPS
    than CPU on this machine. Pass `device="cpu"` explicitly for a reproducible
    benchmark run (doc §8c: vary one axis at a time).
    """
    import torch
    from pyannote.audio import Pipeline

    from multidata.device import best_torch_device

    device = device or best_torch_device()

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

    result = pipeline(str(audio_path), **hints)
    # pyannote.audio 4.x wraps the call result in a `DiarizeOutput` dataclass
    # (`.speaker_diarization` holds the Annotation); older versions returned
    # the Annotation directly. `getattr` handles both without a version pin.
    annotation = getattr(result, "speaker_diarization", result)

    if out_rttm is not None:
        out_rttm = Path(out_rttm)
        out_rttm.parent.mkdir(parents=True, exist_ok=True)
        with open(out_rttm, "w") as f:
            annotation.write_rttm(f)
        log.info("wrote %s  (%d speakers)", out_rttm, len(annotation.labels()))

    return annotation


def turns(annotation):
    """Annotation -> [{start, end, speaker}], sorted by start time."""
    return [
        {"start": segment.start, "end": segment.end, "speaker": label}
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]


def load_rttm(path):
    """Reload a previously-written RTTM back into a pyannote Annotation.

    Lets `asr.py` reuse an already-computed diarization instead of paying for
    pyannote again when both the `diarize` and `asr` stages touch the same
    case (doc §6).
    """
    from pyannote.database.util import load_rttm as _load_rttm

    annotations = _load_rttm(str(path))
    if not annotations:
        raise ValueError(f"{path} has no annotations")
    return next(iter(annotations.values()))


def annotation_to_dataframe(annotation):
    """pyannote Annotation -> the (segment, label, speaker, start, end)
    DataFrame shape whisperx's engine-agnostic `assign_word_speakers` expects
    -- the same shape `whisperx.diarize.DiarizationPipeline.__call__` builds
    internally, so `asr.py` can feed it a diarization from either source."""
    import pandas as pd

    df = pd.DataFrame(annotation.itertracks(yield_label=True),
                       columns=["segment", "label", "speaker"])
    df["start"] = df["segment"].apply(lambda seg: seg.start)
    df["end"] = df["segment"].apply(lambda seg: seg.end)
    return df
