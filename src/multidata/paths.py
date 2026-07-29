"""Repo-root-anchored paths, for notebooks and scripts alike.

Derived from *this file's own location* (`Path(__file__)`), not `Path.cwd()`
or a marker-file search — the editable-installed `multidata` package always
lives inside the checked-out repo at `src/multidata/`, so `ROOT` is correct no
matter what directory a notebook happens to be running from (`notebooks/`,
the repo root, a copied/renamed notebook, whatever). Import these instead of
hand-rolling `"../data/raw"` or a hardcoded absolute path in a notebook — both
are exactly the kind of thing that silently breaks the moment the cwd or the
machine changes.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
MANIFEST_PATH = ROOT / "manifest.sqlite"
