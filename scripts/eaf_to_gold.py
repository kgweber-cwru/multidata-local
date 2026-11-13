#!/usr/bin/env python
"""Hand-annotated .eaf -> <recording>.gold.txt + <recording>.gold.rttm
(docs/transcription_standards.md §4/§11; procedure in annotation/README.md).

The usual call, from a returned and checked pass-2 kit:

    python scripts/eaf_to_gold.py --from-kit annotation/kits/261505/kate

That copies `<recording>.pass2.eaf` to `data/gold/<recording>/<recording>.gold.eaf`
(refusing to overwrite a different gold file there) and takes the recording, the
pass-1 kit, the annotator, the excerpt span and the gold notes from the
pass-2 kit's kit.json. Everything can still be given by hand instead:

    python scripts/eaf_to_gold.py --eaf data/gold/132704/132704.gold.eaf --recording 132704 \
        --kit annotation/kits/132704/kate --annotator kate
    python scripts/run_benchmark.py --recording 132704 --audio data/audio/132704/41.wav \
        --engine whisperx_disfluent --model medium

Redacts real names via the manifest's learner_name/sp_name/preceptor_name
columns (multidata.redact) before anything is written to --out-dir, which
defaults to benchmarks/references/ -- a TRACKED, git-visible location.

If --recording has no row in the manifest, redaction can't run, and this script
**refuses to write** rather than silently risking a real name in a tracked
file -- pass --allow-unredacted to override for an ad hoc recording not yet in the
manifest, and you become responsible for keeping that output out of
git/sharing yourself.

On a successful export with a manifest row, also inserts one row into the
manifest's `gold` table (implementation plan Phase 2.4) -- --annotator/
--gold-at/etc. are optional metadata for that row, not required for the
export itself. Skipped automatically under --allow-unredacted, since there's
no manifest row to attach it to.

Also writes `<recording>.gold.json` beside the text and RTTM: how pass 1 was made
(`blind`, or `seeded` with the seed engine, model and transcript hash --
standards §9), the standards version and the span. `run_benchmark.py` reads
it to refuse scoring the seed engine against its own seeded gold. Pass
`--kit annotation/kits/<recording>/<pass-1 annotator>` so the seed provenance is
taken from the kit; the .eaf's header carries a copy, and the two are
cross-checked.
"""
import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import elan, manifest, seeding  # noqa: E402
from multidata.models import upgrade_keys  # noqa: E402

REFERENCES = ROOT / "benchmarks" / "references"
KITS = ROOT / "annotation" / "kits"
GOLD = ROOT / "data" / "gold"
ROLE_TIERS = {"learner": "LEARNER", "patient": "PATIENT", "preceptor": "PRECEPTOR"}

# The latest version in transcription_standards.md §12's changelog. Not
# auto-read from the doc (parsing a markdown table for one string is more
# fragile than it's worth): bump it here whenever the changelog gets a row.
CURRENT_STANDARDS_VERSION = "1.4"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--from-kit", default=None,
                     help="a returned pass-2 kit (annotation/kits/<recording>/<pass-2 annotator>): "
                          "copies its .eaf into data/gold/ and fills in --eaf, --recording, --kit, "
                          "--annotator, --gold-notes and the span from its kit.json")
    ap.add_argument("--replace-gold", action="store_true",
                     help="with --from-kit: overwrite an existing, different "
                          "data/gold/<recording>/<recording>.gold.eaf")
    ap.add_argument("--eaf", default=None, help="the annotated .eaf to export")
    ap.add_argument("--recording", default=None,
                     help="recording_id -- drives output filenames and the manifest name lookup")
    ap.add_argument("--manifest", default=str(ROOT / "manifest.sqlite"))
    ap.add_argument("--out-dir", default=str(REFERENCES),
                     help=f"default: {REFERENCES} (tracked -- see --allow-unredacted)")
    ap.add_argument("--merge-gap", type=float, default=0.3,
                     help="seconds -- same-tier annotations this close or closer merge "
                          "into one RTTM turn (default 0.3)")
    ap.add_argument("--allow-unredacted", action="store_true",
                     help="write even though --recording has no manifest row (so redaction "
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
    ap.add_argument("--kit", default=None,
                     help="the pass-1 kit folder (annotation/kits/<recording>/<annotator>): "
                          "its kit.json says whether pass 1 was blind or seeded")
    args = ap.parse_args()

    if args.from_kit:
        from_pass2_kit(args)
    if not (args.eaf and args.recording):
        ap.error("give --from-kit, or both --eaf and --recording")

    seed = pass1_provenance(args.eaf, args.kit)
    print(f"pass 1: {seeding.describe(seed)}")

    try:
        names = manifest.names_for_recording(args.recording, args.manifest)
    except KeyError:
        names = None

    out_dir = Path(args.out_dir)
    if names is None:
        msg = (f"No manifest row for recording {args.recording!r} in {args.manifest} -- "
               "redaction could not run, so the output may contain real names.")
        if not args.allow_unredacted:
            sys.exit(
                f"{msg}\nRefusing to write. Pass --allow-unredacted to write anyway "
                f"(then keep {out_dir} out of git/sharing yourself), or add this recording "
                "to the manifest first."
            )
        print(f"WARNING: {msg} Writing anyway (--allow-unredacted).", file=sys.stderr)

    for tier in unredactable_tiers(args.eaf, names):
        print(f"WARNING: the {tier} tier has speech but the manifest has no name for that "
              f"role, so a spoken {tier.lower()} name will NOT be redacted. Read the "
              ".gold.txt before committing it.", file=sys.stderr)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_txt = out_dir / f"{args.recording}.gold.txt"
    out_rttm = out_dir / f"{args.recording}.gold.rttm"

    text, rttm_lines, substitutions = elan.export_gold(
        args.eaf, out_txt=out_txt, out_rttm=out_rttm, names=names, merge_gap=args.merge_gap,
    )

    print(f"wrote {out_txt}  ({len(text.split())} words)")
    print(f"wrote {out_rttm}  ({len(rttm_lines)} turns)")
    if substitutions:
        counted = ", ".join(f"{s['role']}->{s['tag']}" for s in substitutions)
        print(f"redacted {len(substitutions)} name occurrence(s): {counted}")

    gold_at = args.gold_at or datetime.date.today().isoformat()
    meta = {"recording_id": args.recording, "pass1_source": seed.get("pass1_source", "blind"),
            **{k: seed[k] for k in ("seed_engine", "seed_model", "seed_transcript",
                                     "seed_sha256", "roles") if k in seed},
            "standards_version": args.standards_version, "gold_at": gold_at,
            "span": [args.span_start, args.span_end] if args.span_start is not None else None}
    print(f"wrote {seeding.write_gold_meta(args.recording, out_dir, meta)}")

    if names is None:
        print("skipped gold-table row: no manifest row for this recording", file=sys.stderr)
        return

    row_id = manifest.record_gold(
        args.recording, annotator=args.annotator, pass1_frozen_at=args.pass1_frozen_at,
        gold_at=gold_at, standards_version=args.standards_version,
        span_start=args.span_start, span_end=args.span_end, notes=args.gold_notes,
        seed=seed if seed.get("pass1_source") == "seeded" else None, path=args.manifest,
    )
    print(f"recorded gold row id={row_id} in {args.manifest} (gold_at={gold_at})")


def from_pass2_kit(args):
    """Fill args from a pass-2 kit (made by annotation/make_pass2_kit.py) and
    copy its .eaf into data/gold/. Explicit arguments win over the kit's."""
    kit_dir = Path(args.from_kit)
    kj = kit_dir / "kit.json"
    if not kj.exists():
        sys.exit(f"--from-kit {kit_dir}: no kit.json there")
    kit = upgrade_keys(json.loads(kj.read_text()))
    recording = kit["recording_id"]
    pass2 = kit_dir / f"{recording}.pass2.eaf"
    if not pass2.exists():
        sys.exit(f"--from-kit {kit_dir}: no {pass2.name} -- is this a pass-2 kit?")
    pass1_annotator = kit.get("pass1_annotator")
    if not pass1_annotator and not args.kit:
        sys.exit("kit.json doesn't name the pass-1 annotator (a kit made before "
                 "make_pass2_kit.py): add --kit annotation/kits/<recording>/<pass-1 annotator> "
                 "and --annotator")

    check = subprocess.run([sys.executable, str(ROOT / "annotation" / "submit_checks.py"),
                            str(pass2)])
    if check.returncode != 0:
        sys.exit("pass 2 fails submit_checks.py -- fix that first")

    gold = GOLD / recording / f"{recording}.gold.eaf"
    if gold.exists() and gold.read_bytes() != pass2.read_bytes() and not args.replace_gold:
        sys.exit(f"{gold} exists and differs from {pass2}. Pass --replace-gold if the "
                 "returned pass 2 really supersedes it.")
    gold.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pass2, gold)
    print(f"copied {pass2} -> {gold}")

    args.recording = args.recording or recording
    args.eaf = args.eaf or str(gold)
    args.kit = args.kit or str(KITS / recording / pass1_annotator)
    args.annotator = args.annotator or pass1_annotator or ""
    args.gold_notes = args.gold_notes or f"pass 2: {kit.get('annotator', kit_dir.name)}"
    if args.span_start is None and kit.get("span_start") is not None:
        args.span_start, args.span_end = kit["span_start"], kit["span_end"]


def unredactable_tiers(eaf, names):
    """Role tiers that hold speech while the manifest has no name for that
    role -- any name spoken there can't be redacted."""
    sys.path.insert(0, str(ROOT / "annotation"))
    from submit_checks import read_annotations
    spoken = {tier for tier, _s, _e, text in read_annotations(eaf) if text.strip()}
    return [tier for role, tier in ROLE_TIERS.items()
            if tier in spoken and not (names or {}).get(role)]


def pass1_provenance(eaf, kit):
    """How pass 1 was made, from the kit's kit.json and the .eaf header,
    cross-checked. Refuses when they contradict each other."""
    from_eaf = seeding.eaf_provenance(eaf)
    from_kit = {}
    if kit:
        kj = Path(kit) / "kit.json"
        if not kj.exists():
            sys.exit(f"--kit {kit}: no kit.json there")
        from_kit = json.loads(kj.read_text())
    eaf_seeded = from_eaf.get("pass1_source") == "seeded"
    kit_seeded = from_kit.get("pass1_source") == "seeded"
    if eaf_seeded and kit and not kit_seeded:
        sys.exit("the .eaf says pass 1 was seeded but the kit says blind -- wrong --kit?")
    if eaf_seeded and kit_seeded and from_eaf.get("seed_sha256") != from_kit.get("seed_sha256"):
        sys.exit("the .eaf and the kit name different seed transcripts -- wrong --kit?")
    if kit_seeded:
        return from_kit
    if eaf_seeded:
        print("note: seed provenance taken from the .eaf header (no --kit given)")
        return from_eaf
    if not kit:
        print("note: no --kit given and no seed in the .eaf: recorded as blind")
    return {"pass1_source": "blind"}


if __name__ == "__main__":
    sys.exit(main())
