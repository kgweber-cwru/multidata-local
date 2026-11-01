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
