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

# pyannote.audio 4 reports each file's duration and speaker count to
# otel.pyannote.ai unless told not to. Every pyannote caller here loads the
# token through this module first, so switching it off here covers them all
# (pyannote re-reads the variable on every call).
os.environ["PYANNOTE_METRICS_ENABLED"] = "false"


def hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN not set — add it to .env after accepting the "
            "pyannote/speaker-diarization-community-1 terms on huggingface.co"
        )
    return token
