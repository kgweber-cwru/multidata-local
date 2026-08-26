#!/usr/bin/env python
"""Hand-annotated .eaf -> <case>.gold.txt + <case>.gold.rttm
(docs/transcription_standards.md §4/§11).

    python scripts/eaf_to_gold.py --eaf data/gold/132704/132704.pass1.eaf --case 132704 \
        --annotator kate --gold-at 2026-08-26
    python scripts/run_benchmark.py --case 132704 --audio data/audio/132704/41.wav \
        --engine whisperx_disfluent --model medium

Redacts real names via the manifest's learner_name/sp_name/preceptor_name
columns (multidata.redact) before anything is written to --out-dir, which
defaults to benchmarks/references/ -- a TRACKED, git-visible location.

If --case has no row in the manifest, redaction can't run, and this script
**refuses to write** rather than silently risking a real name in a tracked
file -- pass --allow-unredacted to override for an ad hoc case not yet in the
manifest, and you become responsible for keeping that output out of
git/sharing yourself.

On a successful export with a manifest row, also inserts one row into the
manifest's `gold` table (implementation plan Phase 2.4) -- --annotator/
--gold-at/etc. are optional metadata for that row, not required for the
export itself. Skipped automatically under --allow-unredacted, since there's
no manifest row to attach it to.
"""
import argparse
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import elan, manifest  # noqa: E402

REFERENCES = ROOT / "benchmarks" / "references"

# The only version transcription_standards.md has had so far (§12's
# changelog). Not auto-read from the doc -- parsing a markdown table for one
# string is more fragile than it's worth. Bump this default by hand if you
# add a --standards-version yourself and the doc's changelog moves past 1.0.
CURRENT_STANDARDS_VERSION = "1.0"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--eaf", required=True, help="the annotated .eaf to export")
    ap.add_argument("--case", required=True,
                     help="case_id -- drives output filenames and the manifest name lookup")
    ap.add_argument("--manifest", default=str(ROOT / "manifest.sqlite"))
    ap.add_argument("--out-dir", default=str(REFERENCES),
                     help=f"default: {REFERENCES} (tracked -- see --allow-unredacted)")
    ap.add_argument("--merge-gap", type=float, default=0.3,
                     help="seconds -- same-tier annotations this close or closer merge "
                          "into one RTTM turn (default 0.3)")
    ap.add_argument("--allow-unredacted", action="store_true",
                     help="write even though --case has no manifest row (so redaction "
                          "couldn't run) -- you must then keep the output out of any "
                          "tracked/shared location yourself. Also skips the gold-table "
                          "row, which requires a manifest row to attach to.")
    ap.add_argument("--annotator", default="", help="for the manifest's gold table")
    ap.add_argument("--pass1-frozen-at", default="",
                     help="date the blind pass was frozen (standards §9); optional")
    ap.add_argument("--gold-at", default=None,
                     help="date this export represents gold (default: today)")
    ap.add_argument("--standards-version", default=CURRENT_STANDARDS_VERSION)
    ap.add_argument("--span-start", type=float, default=None,
                     help="seconds -- only for excerpt gold (standards §3); omit for "
                          "whole-encounter gold")
    ap.add_argument("--span-end", type=float, default=None)
    ap.add_argument("--gold-notes", default="", help="free text for the manifest's gold table")
    args = ap.parse_args()

    try:
        names = manifest.names_for_case(args.case, args.manifest)
    except KeyError:
        names = None

    out_dir = Path(args.out_dir)
    if names is None:
        msg = (f"No manifest row for case {args.case!r} in {args.manifest} -- "
               "redaction could not run, so the output may contain real names.")
        if not args.allow_unredacted:
            sys.exit(
                f"{msg}\nRefusing to write. Pass --allow-unredacted to write anyway "
                f"(then keep {out_dir} out of git/sharing yourself), or add this case "
                "to the manifest first."
            )
        print(f"WARNING: {msg} Writing anyway (--allow-unredacted).", file=sys.stderr)

    out_txt = out_dir / f"{args.case}.gold.txt"
    out_rttm = out_dir / f"{args.case}.gold.rttm"

    text, rttm_lines, substitutions = elan.export_gold(
        args.eaf, out_txt=out_txt, out_rttm=out_rttm, names=names, merge_gap=args.merge_gap,
    )

    print(f"wrote {out_txt}  ({len(text.split())} words)")
    print(f"wrote {out_rttm}  ({len(rttm_lines)} turns)")
    if substitutions:
        counted = ", ".join(f"{s['role']}->{s['tag']}" for s in substitutions)
        print(f"redacted {len(substitutions)} name occurrence(s): {counted}")

    if names is None:
        print("skipped gold-table row: no manifest row for this case", file=sys.stderr)
        return

    gold_at = args.gold_at or datetime.date.today().isoformat()
    row_id = manifest.record_gold(
        args.case, annotator=args.annotator, pass1_frozen_at=args.pass1_frozen_at,
        gold_at=gold_at, standards_version=args.standards_version,
        span_start=args.span_start, span_end=args.span_end, notes=args.gold_notes,
        path=args.manifest,
    )
    print(f"recorded gold row id={row_id} in {args.manifest} (gold_at={gold_at})")


if __name__ == "__main__":
    sys.exit(main())
