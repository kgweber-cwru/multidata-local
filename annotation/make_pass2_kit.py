#!/usr/bin/env python
"""Build a pass-2 (adjudication) kit from a returned, checked pass 1.

    python annotation/make_pass2_kit.py --recording 261505 --from xiaomei --to kate

Copies `annotation/kits/<recording>/<from>/` to `annotation/kits/<recording>/<to>/`
with the same media, renames `<recording>.pass1.eaf` to `<recording>.pass2.eaf`,
rewrites kit.json (annotator, eaf, pass, pass1_annotator) and writes a
pass-2 README.txt. The pass-1 kit is never modified.

In the manifest's `assignments` table it marks pass 1 returned (today) if it
wasn't yet, and adds the pass-2 row.

Refuses when the two annotators are the same person, when the pass-2 kit or
row already exists, when pass 1 has no row or was abandoned, or when the
pass-1 file fails `submit_checks.py`.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from multidata import manifest
from multidata.models import upgrade_keys

import assignments

ROOT = Path(__file__).resolve().parent.parent
KITS = ROOT / "annotation" / "kits"

README_PASS2 = """Recording {recording_id} -- pass 2 (adjudication)

Open {recording_id}.pass2.eaf: it is {pass1_annotator}'s finished pass 1, with both
media files loaded. Your job is to decide what the audio actually contains.
{window}
Follow the annotator guide's section "Pass 2: adjudication" (on the desktop
under "Guide & cheat sheet"). Listen to the whole encounter; correct words,
boundaries, speakers and markup to the standards; fill in anything pass 1
missed. Do not ask {pass1_annotator} about their choices -- the audio decides.

When you are done, tell the project coordinator.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recording", required=True)
    ap.add_argument("--from", dest="src", required=True, help="pass-1 annotator")
    ap.add_argument("--to", dest="dst", required=True, help="pass-2 annotator")
    ap.add_argument("--manifest", default=str(ROOT / "manifest.sqlite"))
    args = ap.parse_args()

    if args.src == args.dst:
        sys.exit("pass 2 must be done by someone other than the pass-1 annotator")
    src, dst = KITS / args.recording / args.src, KITS / args.recording / args.dst
    pass1 = src / f"{args.recording}.pass1.eaf"
    if not pass1.exists():
        sys.exit(f"no returned pass 1 at {pass1}")
    if dst.exists():
        sys.exit(f"{dst} already exists -- not overwriting it")
    first = manifest.get_assignment(args.recording, args.src, args.manifest)
    if first is None or first["pass"] != 1:
        sys.exit(f"no pass-1 assignment for {args.src} on {args.recording} -- add it with "
                 "annotation/assignments.py add")
    if first["status"] == "abandoned":
        sys.exit(f"{args.src}'s pass 1 of {args.recording} was abandoned")
    if manifest.get_assignment(args.recording, args.dst, args.manifest):
        sys.exit(f"{args.dst} already has an assignment for {args.recording}")

    check = subprocess.run([sys.executable, str(ROOT / "annotation" / "submit_checks.py"),
                            str(pass1)])
    if check.returncode != 0:
        sys.exit("pass 1 fails submit_checks.py -- fix that first")
    if first["status"] in ("assigned", "in_progress"):
        first = assignments.mark_returned(args.recording, args.src, args.manifest)
        print(f"marked {args.src}'s pass 1 returned {first['returned_on']}")

    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        "*.pfsx", "*.eaf.0*", "README.txt", ".DS_Store"))
    (dst / f"{args.recording}.pass1.eaf").rename(dst / f"{args.recording}.pass2.eaf")

    kit = upgrade_keys(json.loads((src / "kit.json").read_text()))
    kit.update({"annotator": args.dst, "eaf": f"{args.recording}.pass2.eaf",
                "pass": 2, "pass1_annotator": args.src})
    kit.setdefault("pass1_source", "blind")
    (dst / "kit.json").write_text(json.dumps(kit, indent=2) + "\n")

    window = ""
    if kit.get("span_start") is not None:
        window = (f"\nThis recording is an EXCERPT: {kit['span_start']:.0f}s to "
                  f"{kit['span_end']:.0f}s, marked on the NOTES tier.\n")
    (dst / "README.txt").write_text(README_PASS2.format(
        recording_id=args.recording, pass1_annotator=args.src, window=window))

    manifest.add_assignment(args.recording, args.dst, 2, kit["pass1_source"], args.manifest,
                            camera=str(kit.get("camera", first["camera"])),
                            assigned_on=assignments.today())
    assignments.backup(args.manifest)

    print(f"pass-2 kit ready: {dst}")


if __name__ == "__main__":
    main()
