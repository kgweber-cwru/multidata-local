"""Best available torch device for the (plain-torch) alignment/diarization
models used by `asr.py`'s whisperx path and `diarize.py` (pipeline doc §6).

Whisper's own decode backend (ctranslate2, used by both whisperx and
faster-whisper) never supports "mps" -- only "cpu"/"cuda". This is only for
the wav2vec2 aligner and pyannote diarizer, which are ordinary torch models.
"""


def best_torch_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"
