#!/usr/bin/env python
"""ASR benchmarking wing — run one config, score it, log provenance (doc §8).

    python benchmarks/run_benchmark.py --case 132704 \
        --audio data/audio/132704/41.wav \
        --engine faster_whisper --model large-v3

Vary ONE axis at a time, everything else pinned (doc §8c). Every run appends a
fully-attributed row to benchmarks/results/runs.csv — a benchmark you can't
attribute to an exact config is noise (doc §8d).

Requires a gold reference at benchmarks/references/<case_id>.txt. Those are
the expensive, essential part; see docs/gold_annotation_guide.md.
"""
import argparse
import csv
import datetime
import hashlib
import platform
import subprocess
import sys
import time
from pathlib import Path

import jiwer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import asr  # noqa: E402
from multidata.elan import words_of  # noqa: E402

RESULTS = ROOT / "benchmarks" / "results" / "runs.csv"
REFERENCES = ROOT / "benchmarks" / "references"

FIELDS = ["timestamp", "case_id", "engine", "model", "audio_sha256",
          "wer", "cer", "wall_s", "git_commit", "machine", "notes"]

# Scoring normalization: casing and punctuation are not what we're measuring.
_NORMALIZE = jiwer.Compose([jiwer.ToLowerCase(), jiwer.RemovePunctuation(),
                            jiwer.RemoveMultipleSpaces(), jiwer.Strip()])


def hypothesis_text(result):
    """Flatten an engine result dict to a plain transcript string."""
    segments = result.get("segments") or []
    if segments and all("text" in s for s in segments):
        return " ".join(s["text"].strip() for s in segments)
    return " ".join(w.get("word", "").strip() for w in words_of(result))


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def record(row):
    """Append-only provenance log; writes the header on first run."""
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    is_new = not RESULTS.exists()
    with open(RESULTS, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--engine", default="faster_whisper", choices=sorted(asr.ENGINES))
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--reference", help="defaults to benchmarks/references/<case>.txt")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    reference_path = Path(args.reference or REFERENCES / f"{args.case}.txt")
    if not reference_path.exists():
        sys.exit(f"No gold reference at {reference_path} — see docs/gold_annotation_guide.md")
    ref = reference_path.read_text()

    kwargs = {} if args.engine == "suite" else {"model_name": args.model}
    started = time.perf_counter()
    result = asr.transcribe(args.audio, engine=args.engine, **kwargs)
    wall_s = time.perf_counter() - started

    hyp = hypothesis_text(result)
    ref_n, hyp_n = _NORMALIZE(ref), _NORMALIZE(hyp)

    row = {
        "timestamp": datetime.datetime.now().isoformat(),
        "case_id": args.case,
        "engine": args.engine,
        "model": args.model,
        "audio_sha256": sha256(args.audio),
        "wer": round(jiwer.wer(ref_n, hyp_n), 4),
        "cer": round(jiwer.cer(ref_n, hyp_n), 4),
        "wall_s": round(wall_s, 1),
        "git_commit": subprocess.getoutput("git rev-parse --short HEAD"),
        "machine": platform.platform(),
        "notes": args.notes,
    }
    record(row)
    print(f"WER {row['wer']}  CER {row['cer']}  ({row['wall_s']}s)  -> {RESULTS}")


if __name__ == "__main__":
    sys.exit(main())
