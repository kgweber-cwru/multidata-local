"""Find external programs (ffmpeg, ffprobe) whether or not the conda env is
activated: PATH first, then the folder of the Python running this code
(a conda env's own bin/), so `~/miniconda3/envs/md-pose/bin/python script.py`
works as well as `conda activate md-pose && python script.py`."""
import shutil
import sys
from pathlib import Path


def which(name):
    """Full path of program `name`; RuntimeError if it isn't installed."""
    path = shutil.which(name) or shutil.which(name, path=str(Path(sys.executable).parent))
    if not path:
        raise RuntimeError(f"{name} not found on PATH or in this env "
                           f"({Path(sys.executable).parent}) -- install ffmpeg into this env")
    return path


def put_env_on_path():
    """Prepend this Python's env folder (its bin/) to PATH, for libraries
    that call programs by bare name (whisperx runs "ffmpeg"). Same effect as
    `conda activate` for programs; harmless if it's already there."""
    import os

    bindir = str(Path(sys.executable).parent)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if bindir not in parts:
        os.environ["PATH"] = os.pathsep.join([bindir] + parts)
