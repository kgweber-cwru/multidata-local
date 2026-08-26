#!/usr/bin/env python
"""The single "where am I" view for the ASR benchmark wing
(docs/asr_provider_implementation_plan.md Phase 2.5).

    python scripts/bench_status.py

Three sections: which cases have gold (and whether the files backing the
manifest's record are actually on disk), what's been scored against them and
with what result, and which (case, engine) cells are still empty. Reads
`manifest.sqlite`'s `gold` table, `benchmarks/references/`, and
`benchmarks/results/runs.csv` -- writes nothing.

This is the thing that keeps a growing sweep legible: a ledger you have to
reconstruct by reading directories is one you stop trusting; a status command
is one you run.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import asr, manifest  # noqa: E402

REFERENCES = ROOT / "benchmarks" / "references"
RESULTS = ROOT / "benchmarks" / "results" / "runs.csv"

LOCAL_ENGINES = sorted(name for name, fn in asr.ENGINES.items())


def gold_status(manifest_path):
    """One entry per case_id that has gold *either* in the manifest's `gold`
    table *or* as files on disk -- the union, not just the manifest side.

    These two can drift in both directions: a manifest row can outlive its
    files (deleted or moved after the fact), and a file can exist with no
    manifest row at all -- e.g. every reference exported by an older
    `eaf_to_gold.py` that predates the gold-table wiring, which is exactly
    the state case 261456 was actually found in the first time this ran.
    Catching only one direction would have missed that.
    """
    from collections import defaultdict

    by_case = defaultdict(list)
    for row in manifest.all_gold(manifest_path):
        by_case[row["case_id"]].append(row)

    disk_cases = {p.name.removesuffix(".gold.txt") for p in REFERENCES.glob("*.gold.txt")}

    report = []
    for case_id in sorted(set(by_case) | disk_cases):
        txt = REFERENCES / f"{case_id}.gold.txt"
        rttm = REFERENCES / f"{case_id}.gold.rttm"
        report.append({
            "case_id": case_id,
            "manifest_rows": by_case.get(case_id, []),
            "txt_exists": txt.exists(),
            "rttm_exists": rttm.exists(),
        })
    return report


def read_runs(results_path):
    """[{case_id, engine, model, wer, cer, source, timestamp}, ...] -- []
    if runs.csv doesn't exist yet (nothing has been scored)."""
    if not Path(results_path).exists():
        return []
    with open(results_path, newline="") as f:
        return list(csv.DictReader(f))


def latest_per_config(runs):
    """One row per (case_id, engine, model) -- the most recent run, since a
    re-run supersedes an earlier one for "what's the current number" even
    though every run stays in runs.csv for history."""
    latest = {}
    for row in runs:
        key = (row["case_id"], row["engine"], row["model"])
        if key not in latest or row["timestamp"] > latest[key]["timestamp"]:
            latest[key] = row
    return latest


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
            print(f"  {entry['case_id']:<12}{flag}")
            continue
        for row in entry["manifest_rows"]:
            span = (f"  [{row['span_start']:.0f}s-{row['span_end']:.0f}s]"
                    if row["span_start"] is not None else "  [whole encounter]")
            print(f"  {entry['case_id']:<12}{span}  gold_at={row['gold_at'] or '?'}"
                  f"  annotator={row['annotator'] or '?'}{flag}")
    print()


def print_runs_section(latest):
    print("=== Latest score per (case, engine, model) ===")
    if not latest:
        print("  (no runs yet)")
        return
    for (case_id, engine, model), row in sorted(latest.items()):
        print(f"  {case_id:<12}{engine:<20}{model:<12}"
              f"WER {row['wer']:<8}CER {row['cer']:<8}({row['source']})")
    print()


def print_matrix_gaps(gold_rows, latest):
    print("=== Empty cells (gold case x local engine, not yet scored) ===")
    gold_cases = sorted({row["case_id"] for row in gold_rows})
    if not gold_cases:
        print("  (no gold cases yet)")
        return
    scored = defaultdict(set)
    for (case_id, engine, _model) in latest:
        scored[case_id].add(engine)

    any_gap = False
    for case_id in gold_cases:
        missing = [e for e in LOCAL_ENGINES if e not in scored[case_id]]
        if missing:
            any_gap = True
            print(f"  {case_id:<12}missing: {', '.join(missing)}")
    if not any_gap:
        print("  (every gold case has been scored against every local engine)")
    print()


def main():
    manifest_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "manifest.sqlite")

    gold_rows = gold_status(manifest_path)
    runs = read_runs(RESULTS)
    latest = latest_per_config(runs)

    print_gold_section(gold_rows)
    print_runs_section(latest)
    print_matrix_gaps(gold_rows, latest)


if __name__ == "__main__":
    sys.exit(main())
