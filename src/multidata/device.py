"""Best available compute device, split by backend (pipeline doc §6).

Two different backends in this codebase have two different device stories, and
conflating them is the easiest way to silently run everything on CPU on a
machine that has a GPU:

- **ctranslate2** (whisper's own decode backend, shared by `faster_whisper`
  and `whisperx`'s transcribe step) only ever supports "cpu"/"cuda" -- never
  "mps". `best_ct2_device`/`best_ct2_compute_type` are for this.
- **plain torch** (the wav2vec2 aligner and pyannote diarizer) supports mps on
  Apple Silicon in addition to cuda/cpu. `best_torch_device` is for this.

The point of splitting these is portability, not just correctness today: on
this Mac, `best_ct2_device()` returns "cpu" (there's no cuda here), so nothing
about current behavior changes. Drop the exact same code onto an NVIDIA box
and `best_ct2_device()` starts returning "cuda" with a matching
`best_ct2_compute_type("cuda") == "float16"` -- no code edit, no `--device`
flag hunting. That's the whole ask: port by moving the process, not by
patching device strings.
"""


def best_torch_device() -> str:
    """mps > cuda > cpu -- for the aligner and diarizer only (see module doc)."""
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def best_ct2_device() -> str:
    """cuda > cpu -- for faster-whisper/whisperx's ctranslate2 decode step.

    Never "mps": ctranslate2 has no Metal backend, so a Mac always lands on
    "cpu" here regardless of `best_torch_device()`'s answer for the
    aligner/diarizer running alongside it in the same process.
    """
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def best_ct2_compute_type(device: str) -> str:
    """int8 is portable and fast everywhere ctranslate2 runs; float16 is
    faster still but CUDA-only, so it's just an on-GPU upgrade rather than a
    separate axis a caller has to think about."""
    return "float16" if device == "cuda" else "int8"
