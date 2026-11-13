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

`--overwrite` rebuilds an existing pass-2 kit from an updated pass 1,
discarding whatever is in the pass-2 kit on the host. It keeps the pass-2
row (noting the rebuild) and, if the pass-1 file changed after its return
date, re-freezes pass 1 today. It refuses once pass 2 is returned or gold.
"""
import argparse
import datetime
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
    ap.add_argument("--overwrite", action="store_true",
                    help="rebuild an existing pass-2 kit from an updated pass 1 "
                         "(discards the pass-2 kit on the host)")
    args = ap.parse_args()

    if args.src == args.dst:
        sys.exit("pass 2 must be done by someone other than the pass-1 annotator")
    src, dst = KITS / args.recording / args.src, KITS / args.recording / args.dst
    pass1 = src / f"{args.recording}.pass1.eaf"
    if not pass1.exists():
        sys.exit(f"no returned pass 1 at {pass1}")
    if dst.exists() and not args.overwrite:
        sys.exit(f"{dst} already exists -- pass --overwrite to rebuild it")
    first = manifest.get_assignment(args.recording, args.src, args.manifest)
    if first is None or first["pass"] != 1:
        sys.exit(f"no pass-1 assignment for {args.src} on {args.recording} -- add it with "
                 "annotation/assignments.py add")
    if first["status"] == "abandoned":
        sys.exit(f"{args.src}'s pass 1 of {args.recording} was abandoned")
    second = manifest.get_assignment(args.recording, args.dst, args.manifest)
    if second and not args.overwrite:
        sys.exit(f"{args.dst} already has an assignment for {args.recording}")
    if second and second["pass"] != 2:
        sys.exit(f"{args.dst} has pass {second['pass']} of {args.recording}, not pass 2")
    if second and second["status"] in ("returned", "gold"):
        sys.exit(f"{args.dst}'s pass 2 of {args.recording} is {second['status']} -- "
                 "not overwriting an adjudication")

    check = subprocess.run([sys.executable, str(ROOT / "annotation" / "submit_checks.py"),
                            str(pass1)])
    if check.returncode != 0:
        sys.exit("pass 1 fails submit_checks.py -- fix that first")
    if first["status"] in ("assigned", "in_progress"):
        first = assignments.mark_returned(args.recording, args.src, args.manifest)
        print(f"marked {args.src}'s pass 1 returned {first['returned_on']}")
    elif args.overwrite:
        changed = datetime.date.fromtimestamp(pass1.stat().st_mtime).isoformat()
        if changed > first["returned_on"]:
            today = assignments.today()
            first = manifest.update_assignment(
                args.recording, args.src, args.manifest, returned_on=today,
                notes=assignments.append_note(
                    first["notes"], f"Pass 1 updated and re-frozen {today} "
                                    f"(first returned {first['returned_on'] or 'undated'})."))
            print(f"pass 1 changed since it was returned: re-frozen {today}")

    if dst.exists():
        shutil.rmtree(dst)
        print(f"removed the old {dst}")
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

    camera = str(kit.get("camera", first["camera"]))
    if second:
        manifest.update_assignment(
            args.recording, args.dst, args.manifest, route=kit["pass1_source"], camera=camera,
            notes=assignments.append_note(
                second["notes"], f"Kit rebuilt {assignments.today()} from an updated pass 1."))
    else:
        manifest.add_assignment(args.recording, args.dst, 2, kit["pass1_source"], args.manifest,
                                camera=camera, assigned_on=assignments.today())
    assignments.backup(args.manifest)

    print(f"pass-2 kit ready: {dst}")


if __name__ == "__main__":
    main()
