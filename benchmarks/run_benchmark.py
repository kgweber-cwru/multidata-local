#!/usr/bin/env python
"""Score saved transcripts against gold (step 2 of 2; `transcribe.py` makes
the transcripts).

    python benchmarks/run_benchmark.py                        # every saved transcript of every gold recording
    python benchmarks/run_benchmark.py --recordings 261505
    python benchmarks/run_benchmark.py --transcript data/transcripts/261505/16_assemblyai_universal-3-5-pro.json

Looks in `data/transcripts/<recording>/` and its `variants/<setting>/`
folders, and appends one row per transcript to benchmarks/results/runs.csv,
the tracked ledger. A transcript already scored with today's method against
today's gold file is skipped (`--rescore` scores it again). `python
scripts/bench_status.py` shows the current numbers.

Scoring (`score()`): the manifest's names for the recording are redacted from
BOTH reference and hypothesis (`multidata.redact`), then both are normalized
with `multidata.normalize` -- L0 verbatim for `wer`/`cer`, L1 filler-neutral
for `wer_l1`. Bracket tags are deleted and numbers canonicalized at both
levels. Excerpt gold (a `span` in the `.gold.json`) is scored on that span
only. A transcript from the engine that seeded a recording's gold is never
scored against it (standards §9).
"""
import argparse
import csv
import datetime
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import jiwer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest, normalize, redact, seeding  # noqa: E402
from multidata.elan import words_of  # noqa: E402
from multidata.models import upgrade_keys  # noqa: E402
from multidata.paths import DATA_DIR  # noqa: E402

RESULTS = ROOT / "benchmarks" / "results" / "runs.csv"
REFERENCES = ROOT / "benchmarks" / "references"

# `source` is history: rows before 2026-11-16 say whether the engine ran live,
# came from the old response cache, or was a saved transcript; every row
# since is a saved transcript. `setting` is the settings.yaml name for a
# variant run ("" for a plain one); `transcript` is the file scored.
FIELDS = ["timestamp", "recording_id", "engine", "model", "audio_sha256",
          "wer", "cer", "wall_s", "source", "git_commit", "machine", "notes",
          "gold_pass1", "wer_l1", "scoring", "gold_sha256", "setting", "transcript"]

# Names the scoring method in each ledger row. Change it whenever score()
# changes in a way that moves numbers, so old and new rows aren't compared.
SCORING = "redact+L0/L1 v4"  # v2: markup off words at L0; L1 drops cut-off words. v3: ok -> okay. v4: numbers canonicalized


def score(ref, hyp, names):
    """WER/CER at L0 and WER at L1, after redacting `names` from both sides.

    Redaction runs on the raw text (it matches names case-sensitively, before
    punctuation is gone); the resulting `[ROLE_NAME]` tags are then deleted
    by normalization like every other bracket tag, so a name counts neither
    for nor against an engine. `names` may be None (no manifest row): then
    nothing is redacted and the gold was exported unredacted too."""
    if names:
        ref, _ = redact.redact_names(ref, names)
        hyp, _ = redact.redact_names(hyp, names)
    ref0, hyp0 = normalize.normalize(ref, "l0"), normalize.normalize(hyp, "l0")
    ref1, hyp1 = normalize.normalize(ref, "l1"), normalize.normalize(hyp, "l1")
    return {
        "wer": round(jiwer.wer(ref0, hyp0), 4),
        "cer": round(jiwer.cer(ref0, hyp0), 4),
        "wer_l1": round(jiwer.wer(ref1, hyp1), 4),
    }


def hypothesis_text(result, span=None):
    """A transcript as plain text; with `span` `(start_s, end_s)`, only the
    words starting inside it (excerpt gold covers part of the audio, and
    words outside it would count as insertions). Filters by word when word
    timing exists, by segment otherwise."""
    segments = result.get("segments") or []
    if span is None:
        if segments and all("text" in s for s in segments):
            return " ".join(s["text"].strip() for s in segments)
        return " ".join(w.get("word", "").strip() for w in words_of(result))

    start, end = span
    words = words_of(result)
    if words:
        return " ".join(w.get("word", "").strip() for w in words
                        if start <= (w.get("start") or 0) < end)
    return " ".join(s["text"].strip() for s in segments if start <= s.get("start", 0) < end)


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def record(row, results_path=RESULTS):
    """Append one row to the ledger, writing the header on first use.
    `results_path` lets a test or a scratch run keep away from the real one."""
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    _upgrade_header(results_path)
    is_new = not results_path.exists()
    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _upgrade_header(results_path):
    """Rewrite an existing ledger whose header predates a column in FIELDS:
    same rows, the new column left empty. A `case_id` column (before the
    rename to LearningSpace's terms) becomes `recording_id`."""
    if not results_path.exists():
        return
    with open(results_path, newline="") as f:
        rows = [upgrade_keys(r) for r in csv.DictReader(f)]
        header = rows[0].keys() if rows else None
    if header is None or list(header) == FIELDS:
        return
    with open(results_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def seed_conflict(engine, meta):
    """Why `engine` may not be scored against this gold, or None: an engine
    that seeded a recording's gold would be scored in its own favour."""
    if meta.get("pass1_source") == "seeded" and engine == meta.get("seed_engine"):
        return (f"its gold was seeded from {engine} ({meta.get('seed_model')}); "
                "score it on blind gold or gold seeded by another engine (standards §9)")
    return None


def describe(path):
    """(engine, model, setting) of a saved transcript. Older local transcripts
    carry no provenance; their model comes from the file name
    (`<camera>_<engine>_<model>.json`)."""
    result = json.loads(Path(path).read_text())
    prov = result.get("provenance") or {}
    engine = prov.get("engine") or result.get("engine")
    if not engine:
        raise ValueError(f"{path} records no engine")
    model = prov.get("model") or Path(path).stem.split(f"_{engine}_", 1)[-1]
    parent = Path(path).parent
    setting = parent.name if parent.parent.name == "variants" else ""
    return result, engine, model, setting


def transcripts_of(recording_id):
    base = DATA_DIR / "transcripts" / recording_id
    found = list(base.glob("*.json")) + list(base.glob("variants/*/*.json"))
    return sorted(p for p in found if not p.name.endswith(".raw.json"))


def scored_already(results_path):
    """{(transcript, gold_sha256)} scored with today's method."""
    if not Path(results_path).exists():
        return set()
    with open(results_path, newline="") as f:
        return {(r.get("transcript"), r.get("gold_sha256")) for r in csv.DictReader(f)
                if r.get("scoring") == SCORING and r.get("transcript")}


def score_transcript(path, recording_id, results_path=RESULTS, manifest_path=manifest.DEFAULT_PATH,
                     references=REFERENCES, notes="", audio_hashes=None):
    """Score one saved transcript and append its ledger row; returns the row,
    or a string saying why it was not scored."""
    reference = Path(references) / f"{recording_id}.gold.txt"
    meta = seeding.read_gold_meta(recording_id, reference.parent)
    result, engine, model, setting = describe(path)
    conflict = seed_conflict(engine, meta)
    if conflict:
        return f"not scored: {conflict}"

    hyp = hypothesis_text(result, span=tuple(meta["span"]) if meta.get("span") else None)
    try:
        names = manifest.names_for_recording(recording_id, manifest_path)
    except KeyError:
        names = None
        print(f"WARNING: no manifest row for {recording_id}: names not redacted before scoring",
              file=sys.stderr)
    scores = score(reference.read_text(), hyp, names)

    prov = result.get("provenance") or {}
    audio = ROOT / prov["audio"] if prov.get("audio") else None
    if audio_hashes is not None and audio is not None and audio.exists():
        audio_sha = audio_hashes.setdefault(audio, sha256(audio))
    else:
        audio_sha = ""
    row = {
        "timestamp": datetime.datetime.now().isoformat(),
        "recording_id": recording_id, "engine": engine, "model": model,
        "audio_sha256": audio_sha, **scores, "wall_s": prov.get("wall_s", ""),
        "source": "transcript", "git_commit": subprocess.getoutput("git rev-parse --short HEAD"),
        "machine": platform.platform(), "notes": notes,
        "gold_pass1": meta.get("pass1_source", "blind"), "scoring": SCORING,
        "gold_sha256": sha256(reference), "setting": setting,
        "transcript": _relative(path),
    }
    record(row, results_path=results_path)
    return row


def _relative(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recordings", nargs="+", help="default: every gold recording")
    ap.add_argument("--transcript", help="score this one file (its recording is in its path)")
    ap.add_argument("--rescore", action="store_true", help="score again even if already current")
    ap.add_argument("--notes", default="")
    ap.add_argument("--results", default=str(RESULTS),
                    help="the ledger; point a scratch run somewhere else")
    args = ap.parse_args()

    if args.transcript:
        path = Path(args.transcript).resolve()
        recording_id = next(p for p in path.parents if p.parent.name == "transcripts").name
        jobs = [(recording_id, path)]
    else:
        recordings = args.recordings or sorted(
            p.name.removesuffix(".gold.txt") for p in REFERENCES.glob("*.gold.txt"))
        jobs = [(r, p) for r in recordings for p in transcripts_of(r)]

    done = set() if args.rescore else scored_already(args.results)
    audio_hashes = {}
    for recording_id, path in jobs:
        reference = REFERENCES / f"{recording_id}.gold.txt"
        if not reference.exists():
            print(f"{recording_id}: no gold at {reference}")
            continue
        if (_relative(path), sha256(reference)) in done:
            continue
        out = score_transcript(path, recording_id, results_path=args.results,
                               notes=args.notes, audio_hashes=audio_hashes)
        label = _relative(path).removeprefix(f"data/transcripts/{recording_id}/")
        if isinstance(out, str):
            print(f"{recording_id:<8} {label:<62} {out}")
        else:
            print(f"{recording_id:<8} {label:<62} WER {out['wer']:.3f} (L1 {out['wer_l1']:.3f})  "
                  f"CER {out['cer']:.3f}")
    print(f"ledger: {args.results}")


if __name__ == "__main__":
    sys.exit(main())
