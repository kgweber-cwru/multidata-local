"""Stage: audio — extract 16 kHz mono WAV for ASR/diarization (doc §5).

Whisper and pyannote both want 16 kHz mono. Extract from whichever camera
carries the cleanest audio (recorded at ingest).

Deliberately no denoising: denoise before benchmarking and you measure your
denoiser, not the ASR (doc §5).
"""
import subprocess
from pathlib import Path

SAMPLE_RATE = 16000


def extract(video_path, out_path, loudnorm=False, overwrite=True):
    """ffmpeg-demux one video's audio track to 16 kHz mono s16 WAV.

    `loudnorm=True` levels cases against each other, which matters for
    benchmark comparability; keep it consistent across a benchmark set.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y" if overwrite else "-n", "-i", str(video_path),
           "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16"]
    if loudnorm:
        cmd += ["-af", "loudnorm"]
    cmd.append(str(out_path))

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path
