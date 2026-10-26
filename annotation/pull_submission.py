#!/usr/bin/env python
"""Bring a finished pass back to the private side and export it as gold.

    python annotation/pull_submission.py --case 261473 --annotator jamie --from-dir <wherever the .eaf came back to>
    python annotation/pull_submission.py --case 261473 --annotator jamie --bucket gs://...

Does three things, in order, and stops at the first that fails:

  1. Gets the submitted .eaf into data/gold/<case>/ -- from a local directory
     (--from-dir, e.g. a laptop mounted as a volume or copied over) or from a
     GCS bucket (--bucket, a holdover from the abandoned VM design's transfer
     path, kept because it still works and costs nothing to leave).
  2. Re-runs the submit checks locally -- whatever produced the submission may
     have already run them, but this is the copy that becomes gold, so it gets
     checked again where it lands.
  3. Runs scripts/eaf_to_gold.py, which redacts names from the manifest, writes
     benchmarks/references/<case>.gold.{txt,rttm}, and records the gold row.

Step 3 is the existing exporter, unchanged. This script is the bit in front of
it: get the file here, confirm it's the file we think it is, hand it over.

Layer 1 rule (transcription_standards.md §8): the .eaf lands under data/gold/,
which is gitignored, and carries real names. Only step 3's output is
publishable.

STATUS (2026-09-23): written for a cloud VM delivery mechanism that has been
torn out -- see docs/current_status.md. --from-dir was always the
mechanism-agnostic path and is the one to use until a replacement is decided.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent


def config(key, default=None):
    """A value from the environment, else a default.

    Used to be backed by annotation/config.sh (deleted along with the rest of
    the VM machinery it configured -- see docs/current_status.md). Left as a
    plain env-var lookup rather than removed, since --bucket/ANNOTATION_BUCKET
    still work exactly as before.
    """
    return os.environ.get(key) or default


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
        bucket = args.bucket or config("ANNOTATION_BUCKET")
        if not bucket:
            raise SystemExit(
                "no bucket: pass --bucket gs://..., set $ANNOTATION_BUCKET, or "
                "use --from-dir instead")
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
