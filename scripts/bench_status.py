#!/usr/bin/env python
"""The single "where am I" view for the ASR benchmark wing
(docs/asr_provider_implementation_plan.md Phase 2.5).

    python scripts/bench_status.py
    python scripts/bench_status.py --markdown    # the findings report's tables

Four sections: which recordings have gold (and whether the files backing the
manifest's record are actually on disk), what's been scored against them and
with what result, each engine setting pooled across recordings, and which
(recording, engine) cells are still empty. `--markdown` prints only the
pooled numbers, as the tables in docs/asr_benchmark_findings_*.md. Reads
`manifest.sqlite`'s `gold` table, `benchmarks/references/`, and
`benchmarks/results/runs.csv` -- writes nothing.

This is the thing that keeps a growing sweep legible: a ledger you have to
reconstruct by reading directories is one you stop trusting; a status command
is one you run.
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest, normalize, seeding, transcribers  # noqa: E402

REFERENCES = ROOT / "benchmarks" / "references"
RESULTS = ROOT / "benchmarks" / "results" / "runs.csv"

ENGINES = transcribers.engines()


def gold_status(manifest_path):
    """One entry per recording_id that has gold *either* in the manifest's `gold`
    table *or* as files on disk -- the union, not just the manifest side.

    These two can drift in both directions: a manifest row can outlive its
    files (deleted or moved after the fact), and a file can exist with no
    manifest row at all -- e.g. every reference exported by an older
    `eaf_to_gold.py` that predates the gold-table wiring, which is exactly
    the state recording 261456 was actually found in the first time this ran.
    Catching only one direction would have missed that.
    """
    from collections import defaultdict

    by_recording = defaultdict(list)
    for row in manifest.all_gold(manifest_path):
        by_recording[row["recording_id"]].append(row)

    disk_recordings = {p.name.removesuffix(".gold.txt") for p in REFERENCES.glob("*.gold.txt")}

    report = []
    for recording_id in sorted(set(by_recording) | disk_recordings):
        txt = REFERENCES / f"{recording_id}.gold.txt"
        rttm = REFERENCES / f"{recording_id}.gold.rttm"
        report.append({
            "recording_id": recording_id,
            "manifest_rows": by_recording.get(recording_id, []),
            "txt_exists": txt.exists(),
            "rttm_exists": rttm.exists(),
        })
    return report


def read_runs(results_path):
    """[{recording_id, engine, model, wer, cer, source, timestamp}, ...] -- []
    if runs.csv doesn't exist yet (nothing has been scored)."""
    if not Path(results_path).exists():
        return []
    with open(results_path, newline="") as f:
        return list(csv.DictReader(f))


def is_current(row, references=REFERENCES):
    """Scored with today's method, against the gold file as it is now on
    disk. Anything else is history: a different normalizer or a since
    re-exported reference gives numbers that can't be compared."""
    sys.path.insert(0, str(ROOT / "benchmarks"))
    from run_benchmark import SCORING, sha256
    gold = Path(references) / f"{row['recording_id']}.gold.txt"
    return (row.get("scoring") == SCORING and gold.exists()
            and row.get("gold_sha256") == sha256(gold))


def latest_per_config(runs):
    """One row per (recording_id, engine, model, setting) -- the most recent
    run, since a re-run supersedes an earlier one for "what's the current
    number" even though every run stays in runs.csv for history."""
    latest = {}
    for row in runs:
        key = (row["recording_id"], row["engine"], row["model"], row.get("setting") or "")
        if key not in latest or row["timestamp"] > latest[key]["timestamp"]:
            latest[key] = row
    return latest


def gold_counts(recording_id, references=REFERENCES):
    """(fillers, backchannels) in a recording's gold, counted as `score()`
    counts them in a transcript."""
    tokens = normalize.normalize((Path(references) / f"{recording_id}.gold.txt").read_text(),
                                 "l0").split()
    return (sum(t in normalize.FILLERS for t in tokens),
            sum(t in normalize.BACKCHANNELS for t in tokens))


def pooled(latest, references=REFERENCES):
    """One entry per (gold kind, engine, model, setting): its recordings
    pooled from the ledger's counts, so each rate is weighted by its own
    denominator (gold words for WER and cpWER, gold speech time for DER).
    Rows without counts (scored before v6) are left out. Sorted by L0 WER."""
    groups = defaultdict(list)
    for (_recording, engine, model, setting), row in latest.items():
        if row.get("words"):
            groups[(row.get("gold_pass1") or "blind", engine, model, setting)].append(row)

    def total(rows, field):
        return sum(float(r[field]) for r in rows if r.get(field) not in (None, ""))

    out = []
    for (kind, engine, model, setting), rows in groups.items():
        words = total(rows, "words")
        gold = [gold_counts(r["recording_id"], references) for r in rows]
        spoken = [r for r in rows if r.get("speech_s")]
        speech = total(spoken, "speech_s")
        cp_words = total(rows, "cp_words")
        out.append({
            "kind": kind, "engine": engine, "model": model, "setting": setting,
            "recordings": len(rows),
            "wer": (total(rows, "sub") + total(rows, "del") + total(rows, "ins")) / words,
            "wer_l1": total(rows, "errors_l1") / total(rows, "words_l1"),
            "sub": total(rows, "sub") / words, "del": total(rows, "del") / words,
            "ins": total(rows, "ins") / words,
            "range": (min(float(r["wer"]) for r in rows), max(float(r["wer"]) for r in rows)),
            "cpwer": total(rows, "cp_errors") / cp_words if cp_words else None,
            "der": (sum(float(r["der"]) * float(r["speech_s"]) for r in spoken) / speech
                    if speech else None),
            "fillers": (int(total(rows, "fillers")), sum(f for f, _ in gold)),
            "backchannels": (int(total(rows, "backchannels")), sum(b for _, b in gold)),
        })
    return sorted(out, key=lambda e: (e["kind"], e["wer"]))


def label(entry):
    return " ".join(x for x in (entry["engine"], entry["model"]) if x) + (
        f" [{entry['setting']}]" if entry["setting"] else "")


def _rate(x, places=3):
    return "" if x is None else f"{x:.{places}f}"


def print_pooled_section(entries):
    print("=== Pooled across recordings (L0 unless marked) ===")
    if not entries:
        print("  (no rows with counts yet: run benchmarks/run_benchmark.py)")
        return
    for kind in ("blind", "seeded"):
        rows = [e for e in entries if e["kind"] == kind]
        if not rows:
            continue
        print(f"  -- against {kind} gold --")
        print(f"  {'':<52}{'n':>3}{'WER':>7}{'L1':>7}{'sub':>7}{'del':>7}{'ins':>7}"
              f"{'range':>11}{'cpWER':>7}{'DER':>7}{'fillers':>11}{'backch':>9}")
        for e in rows:
            print(f"  {label(e):<52}{e['recordings']:>3}{e['wer']:>7.3f}{e['wer_l1']:>7.3f}"
                  f"{e['sub']:>7.1%}{e['del']:>7.1%}{e['ins']:>7.1%}"
                  f"{e['range'][0]:>6.2f}-{e['range'][1]:.2f}{_rate(e['cpwer']):>7}{_rate(e['der']):>7}"
                  f"{'%d/%d' % e['fillers']:>11}{'%d/%d' % e['backchannels']:>9}")
    print()


def print_markdown(entries):
    """The findings report's tables, one set per gold kind."""
    for kind in ("blind", "seeded"):
        rows = [e for e in entries if e["kind"] == kind]
        if not rows:
            continue
        n = {e["recordings"] for e in rows}
        print(f"<!-- against {kind} gold; recordings per row: {sorted(n)} -->\n")
        print("| Engine and setting | L0 WER | L1 WER | Substituted | Missed | Added "
              "| Range across recordings (L0) |")
        print("|---|---|---|---|---|---|---|")
        for e in rows:
            print(f"| {label(e)} | {e['wer']:.3f} | {e['wer_l1']:.3f} | {e['sub']:.1%} "
                  f"| {e['del']:.1%} | {e['ins']:.1%} | {e['range'][0]:.2f}–{e['range'][1]:.2f} |")
        print()
        print("| Engine | WER | cpWER | Cost of attribution | DER |")
        print("|---|---|---|---|---|")
        for e in sorted((e for e in rows if e["cpwer"] is not None), key=lambda e: e["cpwer"]):
            # the cost is the difference of the rates as printed, so the row adds up
            cost = 100 * (round(e["cpwer"], 3) - round(e["wer"], 3))
            print(f"| {label(e)} | {e['wer']:.3f} | {e['cpwer']:.3f} "
                  f"| +{cost:.1f} | {_rate(e['der'])} |")
        print()
        f_gold, b_gold = rows[0]["fillers"][1], rows[0]["backchannels"][1]
        print(f"| Engine | Fillers kept (gold {f_gold}) | Back-channels kept (gold {b_gold}) |")
        print("|---|---|---|")
        for e in rows:
            print(f"| {label(e)} | {e['fillers'][0]} | {e['backchannels'][0]} |")
        print()


def print_gold_section(gold_status_rows):
    print("=== Gold references ===")
    if not gold_status_rows:
        print("  (none found -- run scripts/eaf_to_gold.py)")
        return
    for entry in gold_status_rows:
        problems = []
        if not entry["txt_exists"]:
            problems.append("MISSING .gold.txt")
        if not entry["rttm_exists"]:
            problems.append("missing .gold.rttm")
        if not entry["manifest_rows"]:
            problems.append("no manifest row (predates gold-table wiring, or added by hand)")
        flag = f"  ⚠ {', '.join(problems)}" if problems else ""

        if not entry["manifest_rows"]:
            print(f"  {entry['recording_id']:<12}{flag}")
            continue
        for row in entry["manifest_rows"]:
            span = (f"  [{row['span_start']:.0f}s-{row['span_end']:.0f}s]"
                    if row["span_start"] is not None else "  [whole encounter]")
            kind = seeding.describe(seeding.read_gold_meta(entry["recording_id"], REFERENCES))
            print(f"  {entry['recording_id']:<12}{span}  gold_at={row['gold_at'] or '?'}"
                  f"  annotator={row['annotator'] or '?'}  pass 1: {kind}{flag}")
    print()


def print_runs_section(latest):
    print("=== Latest score per (recording, engine, model, variant) ===")
    if not latest:
        print("  (no runs yet)")
        return
    # blind and seeded gold are reported apart: seeded gold leans toward its
    # seed engine (standards §9), so the two must not be averaged together
    for kind in ("blind", "seeded"):
        rows = [(k, r) for k, r in sorted(latest.items())
                if (r.get("gold_pass1") or "blind") == kind]
        if not rows:
            continue
        print(f"  -- against {kind} gold --")
        for (recording_id, engine, model, setting), row in rows:
            print(f"  {recording_id:<12}{engine:<20}{model:<20}{setting:<20}"
                  f"WER {row['wer']:<8}L1 {row.get('wer_l1') or '':<8}CER {row['cer']}")
    print()


def print_matrix_gaps(gold_rows, latest):
    print("=== Empty cells (gold recording x engine, not yet scored) ===")
    gold_recordings = sorted({row["recording_id"] for row in gold_rows})
    if not gold_recordings:
        print("  (no gold recordings yet)")
        return
    scored = defaultdict(set)
    for (recording_id, engine, _model, _setting) in latest:
        scored[recording_id].add(engine)

    any_gap = False
    for recording_id in gold_recordings:
        missing = [e for e in ENGINES if e not in scored[recording_id]]
        if missing:
            any_gap = True
            print(f"  {recording_id:<12}missing: {', '.join(missing)}")
    if not any_gap:
        print("  (every gold recording has been scored against every engine)")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", nargs="?", default=str(ROOT / "manifest.sqlite"))
    ap.add_argument("--markdown", action="store_true", help="print only the pooled tables, as markdown")
    args = ap.parse_args()

    runs = read_runs(RESULTS)
    current = [r for r in runs if is_current(r)]
    latest = latest_per_config(current)
    if args.markdown:
        print_markdown(pooled(latest))
        return

    gold_rows = gold_status(args.manifest)
    if len(current) < len(runs):
        print(f"({len(runs) - len(current)} older row(s) in runs.csv not shown: scored with "
              "an earlier method, or against gold that has since changed or been withdrawn)\n")

    print_gold_section(gold_rows)
    print_runs_section(latest)
    print_pooled_section(pooled(latest))
    print_matrix_gaps(gold_rows, latest)


if __name__ == "__main__":
    sys.exit(main())
