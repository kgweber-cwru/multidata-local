#!/usr/bin/env python
"""Bring a finished pass back to the private side and export it as gold.

    python annotation/pull_submission.py --case 261473 --annotator jamie

Does three things, in order, and stops at the first that fails:

  1. Downloads the submitted .eaf from the bucket to data/gold/<case>/.
  2. Re-runs the submit checks locally -- the VM already ran them, but this is
     the copy that becomes gold, so it gets checked where it lands.
  3. Runs scripts/eaf_to_gold.py, which redacts names from the manifest, writes
     benchmarks/references/<case>.gold.{txt,rttm}, and records the gold row.

Step 3 is the existing exporter, unchanged. This script is the bit in front of
it: get the file here, confirm it's the file we think it is, hand it over.

Layer 1 rule (transcription_standards.md §8): the .eaf lands under data/gold/,
which is gitignored, and carries real names. Only step 3's output is
publishable.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", required=True)
    ap.add_argument("--annotator", required=True)
    ap.add_argument("--bucket", default=None,
                    help="gs://... (default: $ANNOTATION_BUCKET)")
    ap.add_argument("--from-dir", default=None,
                    help="skip the download; take the submission from here")
    ap.add_argument("--skip-export", action="store_true",
                    help="download and check only, don't run eaf_to_gold.py")
    args, passthrough = ap.parse_known_args()

    dest = ROOT / "data" / "gold" / args.case
    dest.mkdir(parents=True, exist_ok=True)

    if args.from_dir:
        src = Path(args.from_dir)
        for name in (f"{args.case}.pass1.eaf", "kit.json", "submission.json"):
            if (src / name).exists():
                shutil.copy2(src / name, dest / name)
                print(f"copied {name}", flush=True)
    else:
        import os
        bucket = args.bucket or os.environ.get("ANNOTATION_BUCKET")
        if not bucket:
            raise SystemExit(
                "need --bucket gs://... or ANNOTATION_BUCKET in the environment")
        run(["gcloud", "storage", "rsync",
             f"{bucket}/work/{args.case}/{args.annotator}", str(dest)])

    eaf = dest / f"{args.case}.pass1.eaf"
    if not eaf.exists():
        raise SystemExit(f"no {eaf.name} in {dest} -- has it been submitted?")

    span = []
    kit_json = dest / "kit.json"
    if kit_json.exists():
        kit = json.loads(kit_json.read_text())
        if kit.get("span_start") is not None:
            span = ["--span-start", str(kit["span_start"]),
                    "--span-end", str(kit["span_end"])]

    print()
    run([sys.executable, str(HERE / "submit_checks.py"), str(eaf)] + span)

    if args.skip_export:
        print(f"\n{eaf} is here and checks out. Export skipped.")
        return

    print()
    run([sys.executable, str(ROOT / "scripts" / "eaf_to_gold.py"),
         "--eaf", str(eaf), "--case", args.case,
         "--annotator", args.annotator] + span + passthrough)

    print(f"\nDone. Layer 1 is at {eaf} (gitignored, real names).")
    print("Layer 2 is in benchmarks/references/ (tracked, de-identified).")


if __name__ == "__main__":
    main()
