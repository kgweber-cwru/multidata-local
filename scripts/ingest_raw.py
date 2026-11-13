#!/usr/bin/env python
"""Register new raw video files in the manifest.

    python scripts/ingest_raw.py                 # dry run: what would be registered
    python scripts/ingest_raw.py --write         # register them
    python scripts/ingest_raw.py --write data/raw/20260224-155704-v309038-13.mp4

Any env with ffprobe on PATH (md-utility, md-speech; md-pose since 2026-10-31).
With no file arguments it looks at every .mp4 in data/raw/ that the manifest
doesn't have yet. For each file:

  1. check the name follows <date>-<time>-<file_number>-<camera>.mp4
     (ingest.parse_filename)
  2. find its recording from the recording time and the camera's room
     (filematch.resolve) -- the recording must already be in the manifest, from the
     source system's export (python -m multidata.manifest <export.xlsx>)
  3. read duration, frame rate, size, audio, and a SHA-256 fingerprint
     (ingest.summarize); refuse a file whose fingerprint is already registered
  4. with --write, add it with every stage pending (manifest.add_video)

Step 2 caches its answer (manifest table file_matches) even in a dry
run; that's harmless. Files that can't be matched are listed at the end with
the reason (usually a camera missing from the camera->room table).
notebooks/ingest_walkthrough.ipynb goes through the same steps one at a time.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from multidata import filematch, ingest, manifest  # noqa: E402
from multidata.paths import MANIFEST_PATH, RAW_DIR  # noqa: E402


def unregistered(db=MANIFEST_PATH):
    """.mp4 files in data/raw/ the manifest doesn't know, oldest name first."""
    known = {Path(v.filepath).name for v in manifest.all_videos(db)}
    return sorted(p for p in RAW_DIR.glob("*.mp4") if p.name not in known)


def register(path, write=False, db=MANIFEST_PATH):
    """Steps 1-4 for one file. Returns (recording_id, summary) or raises
    ValueError / filematch.MatchError with the reason."""
    ingest.parse_filename(path)                                  # 1
    recording_id = filematch.resolve(path, manifest_path=db)          # 2
    info = ingest.summarize(path)                                # 3
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dup = c.execute("SELECT recording_id, camera FROM videos WHERE sha256 = ?",
                    (info["sha256"],)).fetchone()
    if dup:
        raise ValueError(f"same file already registered as {dup[0]}/{dup[1]}")
    if write:
        manifest.add_video(recording_id=recording_id, path=db, **info)     # 4
    return recording_id, info


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", help="default: every unregistered .mp4 in data/raw/")
    ap.add_argument("--write", action="store_true", help="register (default is a dry run)")
    ap.add_argument("--manifest", default=MANIFEST_PATH)
    args = ap.parse_args()

    files = [Path(f) for f in args.files] or unregistered(args.manifest)
    if not files:
        print("nothing new in data/raw/")
        return 0
    ok, problems = [], []
    for f in files:
        try:
            recording_id, info = register(f, args.write, args.manifest)
        except (ValueError, KeyError, filematch.MatchError) as e:
            problems.append((f.name, f"{type(e).__name__}: {e}"))
            continue
        ok.append(recording_id)
        print(f"{'registered' if args.write else 'would register'} {f.name} -> recording {recording_id}, "
              f"camera {info['camera']}, {info['duration_s'] / 60:.1f} min, "
              f"{info['fps']:.2f} fps, {info['width']}x{info['height']}, "
              f"audio {'yes' if info['has_audio'] else 'NO'}")
    for name, why in problems:
        print(f"PROBLEM {name}: {why}")
    if ok and args.write:
        c = sqlite3.connect(f"file:{args.manifest}?mode=ro", uri=True)
        for recording_id in sorted(set(ok)):
            cams = [r[0] for r in c.execute("SELECT camera FROM videos WHERE recording_id = ?", (recording_id,))]
            print(f"recording {recording_id}: cameras {', '.join(sorted(cams))}"
                  + ("" if len(cams) == 2 else "  <- not a two-camera pair; no 3D for this recording"))
        print("next: run the stages (README quick start, or the walkthrough notebook)")
    elif ok:
        print(f"{len(ok)} file(s) ready; add --write to register them")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
