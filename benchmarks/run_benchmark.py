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
> (spec §8). Not yet reconciled (Phase 6).
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

from multidata import asr, bench_runs  # noqa: E402
from multidata.elan import words_of  # noqa: E402

RESULTS = ROOT / "benchmarks" / "results" / "runs.csv"
REFERENCES = ROOT / "benchmarks" / "references"
RUNS_ROOT = ROOT / "benchmarks" / "runs"
CACHE_ROOT = ROOT / "benchmarks" / "cache"

# "source" distinguishes three ways a result reached the scorer -- without
# this, a near-zero wall_s (cache_hit/cached) would look like a suspiciously
# fast engine rather than what it is: no engine ran at all.
#   live      -- the engine actually ran just now
#   cache_hit -- a prior identical (engine, model, audio) call was reused;
#                the engine would have run, but didn't need to
#   cached    -- an externally supplied --transcript file was scored; some
#                other process (a notebook, run_stage.py) produced it
FIELDS = ["timestamp", "case_id", "engine", "model", "audio_sha256",
          "wer", "cer", "wall_s", "source", "git_commit", "machine", "notes"]

# Scoring normalization: casing and punctuation are not what we're measuring.
_NORMALIZE = jiwer.Compose([jiwer.ToLowerCase(), jiwer.RemovePunctuation(),
                            jiwer.RemoveMultipleSpaces(), jiwer.Strip()])


def hypothesis_text(result, span=None):
    """Flatten an engine result dict to a plain transcript string.

    `span`, if given, is `(start_s, end_s)` -- only text starting inside that
    window is included. Required for excerpt gold (docs/transcription_
    standards.md §3): the reference file already covers only the excerpt (it
    was exported that way), but a live/cached hypothesis normally covers the
    *whole* audio -- score it unfiltered against an excerpt reference and
    every word outside the excerpt shows up as a spurious insertion,
    inflating WER for no real reason.

    Filters at word level when word-level timing exists (the common case),
    for a boundary that doesn't depend on where a segment happened to be cut.
    Falls back to segment-level filtering only when there's no word-level
    timing to filter with (`word_timing: false` -- asr_provider_spec.md §4)
    -- coarser, but the best available without per-word timestamps.
    """
    segments = result.get("segments") or []
    if span is None:
        if segments and all("text" in s for s in segments):
            return " ".join(s["text"].strip() for s in segments)
        return " ".join(w.get("word", "").strip() for w in words_of(result))

    start, end = span
    words = words_of(result)
    if words:
        return " ".join(
            w.get("word", "").strip() for w in words
            if start <= w.get("start", 0) < end
        )
    return " ".join(
        s["text"].strip() for s in segments
        if start <= s.get("start", 0) < end
    )


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def record(row, results_path=RESULTS):
    """Append-only provenance log; writes the header on first run.

    `results_path` defaults to the real ledger (`RESULTS`) but is a
    parameter, not a hardcoded reach at module scope, specifically so a test
    or a manual smoke run can redirect it -- every other output path
    (`--reference`, `--out-root`, `--cache-root`) already had this, and this
    one didn't, which is exactly how a synthetic smoke-test row twice ended
    up in the real runs.csv before being caught and cleaned up by hand.
    """
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not results_path.exists()
    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def load_result(args, audio_sha256, cache_root=None):
    """`(result, engine, source, wall_s)` -- either run `args.engine` live
    (checking `cache_root` first, if given), or load `args.transcript` from
    disk and skip the engine entirely.

    In cached-transcript mode, the transcript's *own* `engine` field (which
    `asr.transcribe()` stamps, but a bare `transcribe_whisperx_disfluent()`-
    style call does not) is the source of truth for the provenance row. If
    it's present and `--engine` disagrees, that's a note, not an error --
    the recorded field wins. If it's **absent**, `--engine` must be given
    explicitly (`args.engine is not None`, i.e. actually typed, not just
    defaulted) -- refusing rather than silently falling back to
    `asr.DEFAULT_ENGINE` is the whole point: a missing field with a silent
    guess produces a confidently-wrong provenance row with no signal
    anything was off, which is worse than an error.

    `audio_sha256` is a parameter, not recomputed here, so a caller that also
    needs it for the provenance row only hashes the (possibly large) audio
    file once.
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

    cache_params = dict(kwargs)
    key = bench_runs.cache_key(audio_sha256, engine, cache_params) if cache_root else None
    if key is not None:
        cached_result = bench_runs.cache_get(cache_root, key)
        if cached_result is not None:
            return cached_result, engine, "cache_hit", 0.0

    started = time.perf_counter()
    result = asr.transcribe(args.audio, engine=engine, **kwargs)
    wall_s = time.perf_counter() - started

    if key is not None:
        bench_runs.cache_put(cache_root, key, result)

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
    ap.add_argument("--span-start", type=float, default=None,
                     help="seconds -- for excerpt gold (standards §3), trims the hypothesis "
                          "to this window before scoring, since the reference only covers "
                          "the excerpt but the hypothesis normally covers the whole audio. "
                          "Omit for whole-encounter gold. Requires --span-end.")
    ap.add_argument("--span-end", type=float, default=None)
    ap.add_argument("--notes", default="")
    ap.add_argument("--out-root", default=str(RUNS_ROOT),
                     help=f"default: {RUNS_ROOT} -- per-run directory holding the resolved "
                          "config, the normalized record, and scores.json (implementation "
                          "plan Phase 2.1). runs.csv stays the fast-glance ledger; this is "
                          "the full detail behind each row.")
    ap.add_argument("--cache-root", default=str(CACHE_ROOT),
                     help=f"default: {CACHE_ROOT} -- reuse a prior identical (engine, model, "
                          "audio) live result instead of re-running the engine. Ignored in "
                          "--transcript mode.")
    ap.add_argument("--no-cache", action="store_true",
                     help="always run the engine live, even if a cached result exists -- for "
                          "a genuine timing measurement, or a non-deterministic engine")
    ap.add_argument("--results", default=str(RESULTS),
                     help=f"default: {RESULTS} -- the append-only provenance ledger. "
                          "Override for a smoke test or a scratch sweep so it never touches "
                          "the real one.")
    args = ap.parse_args()

    if (args.span_start is None) != (args.span_end is None):
        sys.exit("--span-start and --span-end must be given together")
    span = (args.span_start, args.span_end) if args.span_start is not None else None
    cache_root = None if args.no_cache else args.cache_root

    reference_path = Path(args.reference or REFERENCES / f"{args.case}.gold.txt")
    if not reference_path.exists():
        sys.exit(f"No gold reference at {reference_path} — see docs/transcription_standards.md")
    ref = reference_path.read_text()

    audio_sha256 = sha256(args.audio)
    result, engine, source, wall_s = load_result(args, audio_sha256, cache_root=cache_root)

    hyp = hypothesis_text(result, span=span)
    ref_n, hyp_n = _NORMALIZE(ref), _NORMALIZE(hyp)

    wer = round(jiwer.wer(ref_n, hyp_n), 4)
    cer = round(jiwer.cer(ref_n, hyp_n), 4)

    row = {
        "timestamp": datetime.datetime.now().isoformat(),
        "case_id": args.case,
        "engine": engine,
        "model": args.model,
        "audio_sha256": audio_sha256,
        "wer": wer,
        "cer": cer,
        "wall_s": round(wall_s, 1),
        "source": source,
        "git_commit": subprocess.getoutput("git rev-parse --short HEAD"),
        "machine": platform.platform(),
        "notes": args.notes,
    }
    record(row, results_path=args.results)

    run_config = {
        "case_id": args.case, "engine": engine, "model": args.model, "source": source,
        "audio_sha256": audio_sha256, "reference": str(reference_path),
        "span": list(span) if span else None, "transcript": args.transcript,
    }
    run_id = bench_runs.make_run_id(engine, args.model, run_config)
    run_dir = bench_runs.write_run(args.out_root, run_id, run_config, result,
                                    {"wer": wer, "cer": cer})

    print(f"WER {wer}  CER {cer}  ({row['wall_s']}s, {source})")
    print(f"  ledger: {args.results}")
    print(f"  run:    {run_dir}")


if __name__ == "__main__":
    sys.exit(main())
