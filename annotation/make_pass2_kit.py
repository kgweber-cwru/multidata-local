#!/usr/bin/env python
"""Build a pass-2 (adjudication) kit from a returned, checked pass 1.

    python annotation/make_pass2_kit.py --case 261505 --from xiaomei --to kate

Copies `annotation/kits/<case>/<from>/` to `annotation/kits/<case>/<to>/`
with the same media, renames `<case>.pass1.eaf` to `<case>.pass2.eaf`,
rewrites kit.json (annotator, eaf, pass, pass1_annotator) and writes a
pass-2 README.txt. The pass-1 kit is never modified.

Refuses when the two annotators are the same person, when the pass-2 kit
already exists, or when the pass-1 file fails `submit_checks.py`.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KITS = ROOT / "annotation" / "kits"

README_PASS2 = """Case {case_id} -- pass 2 (adjudication)

Open {case_id}.pass2.eaf: it is {pass1_annotator}'s finished pass 1, with both
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
    ap.add_argument("--case", required=True)
    ap.add_argument("--from", dest="src", required=True, help="pass-1 annotator")
    ap.add_argument("--to", dest="dst", required=True, help="pass-2 annotator")
    args = ap.parse_args()

    if args.src == args.dst:
        sys.exit("pass 2 must be done by someone other than the pass-1 annotator")
    src, dst = KITS / args.case / args.src, KITS / args.case / args.dst
    pass1 = src / f"{args.case}.pass1.eaf"
    if not pass1.exists():
        sys.exit(f"no returned pass 1 at {pass1}")
    if dst.exists():
        sys.exit(f"{dst} already exists -- not overwriting it")

    check = subprocess.run([sys.executable, str(ROOT / "annotation" / "submit_checks.py"),
                            str(pass1)])
    if check.returncode != 0:
        sys.exit("pass 1 fails submit_checks.py -- fix that first")

    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        "*.pfsx", "*.eaf.0*", "README.txt", ".DS_Store"))
    (dst / f"{args.case}.pass1.eaf").rename(dst / f"{args.case}.pass2.eaf")

    kit = json.loads((src / "kit.json").read_text())
    kit.update({"annotator": args.dst, "eaf": f"{args.case}.pass2.eaf",
                "pass": 2, "pass1_annotator": args.src})
    kit.setdefault("pass1_source", "blind")
    (dst / "kit.json").write_text(json.dumps(kit, indent=2) + "\n")

    window = ""
    if kit.get("span_start") is not None:
        window = (f"\nThis case is an EXCERPT: {kit['span_start']:.0f}s to "
                  f"{kit['span_end']:.0f}s, marked on the NOTES tier.\n")
    (dst / "README.txt").write_text(README_PASS2.format(
        case_id=args.case, pass1_annotator=args.src, window=window))

    print(f"pass-2 kit ready: {dst}")


if __name__ == "__main__":
    main()
