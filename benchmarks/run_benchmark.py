#!/usr/bin/env python
"""ASR benchmarking wing — run one config, score it, log provenance (doc §8).

    python benchmarks/run_benchmark.py --case 132704 \
        --audio data/audio/132704/41.wav \
        --engine faster_whisper --model large-v3

    # score a transcript already on disk instead of re-running the engine --
    # e.g. one run_stage.py already produced, or a slow engine you don't want
    # to pay for twice:
    python benchmarks/run_benchmark.py --case 132704 \
        --audio data/audio/132704/41.wav \
        --transcript data/transcripts/132704/41_whisperx_disfluent_medium.json \
        --model medium

Vary ONE axis at a time, everything else pinned (doc §8c). Every run appends a
fully-attributed row to benchmarks/results/runs.csv — a benchmark you can't
attribute to an exact config is noise (doc §8d).

Requires a gold reference at benchmarks/references/<case_id>.gold.txt (see
scripts/eaf_to_gold.py to produce one from an annotated .eaf). Those are the
expensive, essential part; see docs/transcription_standards.md.

> Predates the multi-provider design in docs/asr_provider_spec.md: this script
> still computes a single WER/CER pair, where the spec calls for a dual report
> (L0 verbatim + L1 filler-neutral, spec §6) and a wider provenance row
> (spec §8). It also writes into benchmarks/results/ directly rather than a
> per-run directory (spec §7). Not yet reconciled.
"""
import argparse
import csv
import datetime
import hashlib
import json
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

# "source" distinguishes a live engine run from one scored off an existing
# transcript -- without it, a cached run's near-zero wall_s (just the JSON
# load + scoring, not a real transcribe) would look like a suspiciously fast
# engine rather than what it is: no engine ran at all.
FIELDS = ["timestamp", "case_id", "engine", "model", "audio_sha256",
          "wer", "cer", "wall_s", "source", "git_commit", "machine", "notes"]

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


def load_result(args):
    """`(result, engine, source, wall_s)` -- either run `args.engine` live, or
    load `args.transcript` from disk and skip the engine entirely.

    In cached mode, the transcript's *own* `engine` field (which
    `asr.transcribe()` stamps, but a bare `transcribe_whisperx_disfluent()`-
    style call does not) is the source of truth for the provenance row. If
    it's present and `--engine` disagrees, that's a note, not an error --
    the recorded field wins. If it's **absent**, `--engine` must be given
    explicitly (`args.engine is not None`, i.e. actually typed, not just
    defaulted) -- refusing rather than silently falling back to
    `asr.DEFAULT_ENGINE` is the whole point: a missing field with a silent
    guess produces a confidently-wrong provenance row with no signal
    anything was off, which is worse than an error.
    """
    if args.transcript:
        transcript_path = Path(args.transcript)
        if not transcript_path.exists():
            sys.exit(f"No transcript at {transcript_path}")
        with open(transcript_path) as f:
            result = json.load(f)
        recorded_engine = result.get("engine")

        if recorded_engine:
            if args.engine and args.engine != recorded_engine:
                print(f"note: scoring transcript's own engine={recorded_engine!r}, "
                      f"not --engine={args.engine!r}", file=sys.stderr)
            engine = recorded_engine
        elif args.engine:
            engine = args.engine
        else:
            sys.exit(
                f"{transcript_path} has no 'engine' field recorded -- it was likely "
                "produced by calling a transcribe_*() function directly rather than "
                "asr.transcribe(), which stamps this. Pass --engine explicitly so the "
                "provenance row isn't a silent guess (this bit Kate on case 261456: "
                "two rows silently recorded as faster_whisper when the transcript was "
                "actually whisperx_disfluent)."
            )

        # No engine ran, so there's no transcription time to report -- 0.0,
        # not a timed-but-meaningless JSON-load duration (see FIELDS comment
        # on why `source` exists to disambiguate this from a genuinely fast
        # live run rather than this field carrying a misleading non-zero
        # number).
        return result, engine, "cached", 0.0

    engine = args.engine or asr.DEFAULT_ENGINE
    kwargs = {} if engine == "suite" else {"model_name": args.model}
    started = time.perf_counter()
    result = asr.transcribe(args.audio, engine=engine, **kwargs)
    wall_s = time.perf_counter() - started
    return result, engine, "live", wall_s


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", required=True)
    ap.add_argument("--audio", required=True,
                     help="for provenance hashing (doc §8a) -- required even with --transcript")
    ap.add_argument("--engine", default=None, choices=sorted(asr.ENGINES),
                     help=f"engine to run live (default: {asr.DEFAULT_ENGINE}); with "
                          "--transcript, only used when the transcript has no recorded "
                          "engine of its own, and REQUIRED in that case -- no silent guess")
    ap.add_argument("--model", default="large-v3",
                     help="for the provenance row -- a transcript JSON doesn't record its "
                          "own model size, so this isn't inferred from --transcript")
    ap.add_argument("--transcript",
                     help="score an existing transcript JSON instead of running --engine live "
                          "(e.g. data/transcripts/<case>/<camera>_<engine>_<model>.json)")
    ap.add_argument("--reference", help="defaults to benchmarks/references/<case>.gold.txt")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    reference_path = Path(args.reference or REFERENCES / f"{args.case}.gold.txt")
    if not reference_path.exists():
        sys.exit(f"No gold reference at {reference_path} — see docs/transcription_standards.md")
    ref = reference_path.read_text()

    result, engine, source, wall_s = load_result(args)

    hyp = hypothesis_text(result)
    ref_n, hyp_n = _NORMALIZE(ref), _NORMALIZE(hyp)

    row = {
        "timestamp": datetime.datetime.now().isoformat(),
        "case_id": args.case,
        "engine": engine,
        "model": args.model,
        "audio_sha256": sha256(args.audio),
        "wer": round(jiwer.wer(ref_n, hyp_n), 4),
        "cer": round(jiwer.cer(ref_n, hyp_n), 4),
        "wall_s": round(wall_s, 1),
        "source": source,
        "git_commit": subprocess.getoutput("git rev-parse --short HEAD"),
        "machine": platform.platform(),
        "notes": args.notes,
    }
    record(row)
    print(f"WER {row['wer']}  CER {row['cer']}  ({row['wall_s']}s, {source})  -> {RESULTS}")


if __name__ == "__main__":
    sys.exit(main())
