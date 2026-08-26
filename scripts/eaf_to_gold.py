#!/usr/bin/env python
"""Hand-annotated .eaf -> <case>.gold.txt + <case>.gold.rttm
(docs/transcription_standards.md §4/§11).

    python scripts/eaf_to_gold.py --eaf data/gold/132704/132704.pass1.eaf --case 132704
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
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import elan, manifest  # noqa: E402

REFERENCES = ROOT / "benchmarks" / "references"


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
                          "tracked/shared location yourself")
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


if __name__ == "__main__":
    sys.exit(main())
