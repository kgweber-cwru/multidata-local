"""Shared HuggingFace token loading for gated models (whisperx diarization,
`diarize.py`'s pyannote pipeline).

`use_auth_token=True` (the old whisperx/pyannote default) relies on a prior
interactive `huggingface-cli login` and is deprecated by huggingface_hub in
favor of passing an explicit token. This reads `HF_TOKEN` from `.env`
(gitignored) instead, so the token travels with the repo checkout rather than
whatever happens to be cached in `~/.cache/huggingface` on a given machine.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN not set — add it to .env after accepting the "
            "pyannote/speaker-diarization-3.1 terms on huggingface.co"
        )
    return token
