#!/usr/bin/env python
"""Who has which annotation pass: the manifest's `assignments` table.

    python annotation/assignments.py list [--recording R]
    python annotation/assignments.py return R ANNOTATOR      # step 4: check the file, mark it returned
    python annotation/assignments.py note R ANNOTATOR "text"  # append to the row's notes (never a name)
    python annotation/assignments.py set R ANNOTATOR --status abandoned   # any other change
    python annotation/assignments.py add R ANNOTATOR --pass 1 --route blind   # a row no script made
    python annotation/assignments.py check                    # rows vs kits, gold and the manifest

make_kit.py, make_pass2_kit.py and eaf_to_gold.py write their own rows, so
this is for the steps between them. Every change rewrites
`db_seed/assignments.csv`, the git-tracked backup (the database is
gitignored); `import` loads that file back into an empty table.

A row is keyed by (recording, annotator), as its kit folder is:
`annotation/kits/<recording>/<annotator>/<recording>.pass<N>.eaf`.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest  # noqa: E402
from multidata.models import upgrade_keys  # noqa: E402
from submit_checks import check as check_annotations, read_annotations  # noqa: E402

KITS = ROOT / "annotation" / "kits"
GOLD = ROOT / "data" / "gold"
MANIFEST = ROOT / "manifest.sqlite"
BACKUP = ROOT / "db_seed" / "assignments.csv"


def today():
    return datetime.date.today().isoformat()


def kit_file(row, kits=None):
    r = row["recording_id"]
    return (kits or KITS) / r / row["annotator"] / f"{r}.pass{row['pass']}.eaf"


def gold_file(recording_id, gold=None):
    return (gold or GOLD) / str(recording_id) / f"{recording_id}.gold.eaf"


def backup(manifest_path):
    """Rewrite the git-tracked CSV -- only for the real manifest, so a test
    or scratch database never overwrites it."""
    if Path(manifest_path).resolve() == MANIFEST.resolve():
        manifest.export_assignments(BACKUP, manifest_path)


def append_note(notes, text):
    return f"{notes} {text}".strip() if notes else text


def problems_in(eaf):
    """submit_checks.py's refusals for one file ([] = it passes)."""
    return check_annotations(read_annotations(eaf))[0]


def mark_returned(recording_id, annotator, manifest_path, on=None):
    """Set a row returned (once its file has passed the checks). Returns the row."""
    row = manifest.get_assignment(recording_id, annotator, manifest_path)
    if row is None:
        raise KeyError(f"no assignment for {annotator} on {recording_id}")
    if row["status"] not in ("assigned", "in_progress", "returned"):
        raise ValueError(f"{annotator}'s pass {row['pass']} of {recording_id} is "
                         f"{row['status']}, not out for work")
    row = manifest.update_assignment(recording_id, annotator, manifest_path, status="returned",
                                     returned_on=on or row["returned_on"] or today())
    backup(manifest_path)
    return row


def audit(manifest_path, kits=None, gold=None):
    """Everything about the table that disagrees with the files on disk or
    the manifest's gold table, as a list of strings ([] = consistent)."""
    kits, gold = kits or KITS, gold or GOLD
    rows = manifest.all_assignments(manifest_path)
    by_key = {(r["recording_id"], r["annotator"]): r for r in rows}
    gold_rows = {g["recording_id"] for g in manifest.all_gold(manifest_path)}
    out = []

    for kit in sorted(p for p in kits.glob("*/*") if p.is_dir()):
        if (kit.parent.name, kit.name) not in by_key:
            out.append(f"{kit.parent.name} {kit.name}: kit folder {kit.relative_to(kits.parent)} "
                       "has no row (add it, or delete the kit)")

    for r in rows:
        who = f"{r['recording_id']} {r['annotator']} pass {r['pass']}"
        for col in ("assigned_on", "returned_on"):
            if r[col]:
                try:
                    datetime.date.fromisoformat(r[col])
                except ValueError:
                    out.append(f"{who}: {col} {r[col]!r} is not YYYY-MM-DD")
        if r["status"] == "abandoned":
            continue
        f = kit_file(r, kits)
        if not f.exists():
            out.append(f"{who}: no {f.relative_to(kits.parent)}")
            continue
        kj = f.parent / "kit.json"
        if kj.exists():
            kit = upgrade_keys(json.loads(kj.read_text()))
            if r["camera"] and str(kit.get("camera", "")) != r["camera"]:
                out.append(f"{who}: camera {r['camera']} but kit.json says {kit.get('camera')}")
            if kit.get("pass1_source", "blind") != r["route"]:
                out.append(f"{who}: route {r['route']} but kit.json says "
                           f"{kit.get('pass1_source', 'blind')}")
        if r["status"] in ("returned", "gold"):
            if not r["returned_on"]:
                out.append(f"{who}: {r['status']} with no returned_on")
            bad = problems_in(f)
            if bad:
                out.append(f"{who}: {r['status']}, but the file fails submit_checks "
                           f"({len(bad)} problem(s), first: {bad[0]})")
            changed = datetime.date.fromtimestamp(f.stat().st_mtime).isoformat()
            if r["returned_on"] and changed > r["returned_on"]:
                out.append(f"{who}: file changed {changed}, after it was returned "
                           f"{r['returned_on']} (a frozen pass was edited?)")
        if r["pass"] == 2:
            firsts = [p for p in rows if p["recording_id"] == r["recording_id"]
                      and p["pass"] == 1 and p["status"] in ("returned", "gold")]
            if not firsts:
                out.append(f"{who}: no returned pass 1 for this recording")
            elif any(p["route"] != r["route"] for p in firsts):
                out.append(f"{who}: route {r['route']} differs from its pass 1")
        if r["status"] == "gold":
            g = gold_file(r["recording_id"], gold)
            if not g.exists():
                out.append(f"{who}: gold, but no {g.relative_to(gold.parent.parent)}")
            elif g.read_bytes() != f.read_bytes():
                out.append(f"{who}: {g.name} differs from the pass-2 file")
            if r["recording_id"] not in gold_rows:
                out.append(f"{who}: gold, but the manifest's gold table has no row")

    gold_status = {r["recording_id"] for r in rows if r["status"] == "gold"}
    for g in sorted(gold.glob("*/*.gold.eaf")):
        if g.parent.name not in gold_status:
            out.append(f"{g.parent.name}: {g.name} exists but no row is gold")
    return out


def cmd_list(args):
    rows = manifest.all_assignments(args.manifest, args.recording)
    minutes = {(v.recording_id, v.camera): v.duration_s for v in manifest.all_videos(args.manifest)}
    print(f"{'recording':<10} {'cam':>4} {'min':>5} {'pass':>4}  {'route':<7} {'annotator':<10} "
          f"{'status':<11} {'assigned':<10} {'returned':<10} notes")
    for r in rows:
        d = minutes.get((r["recording_id"], r["camera"]))
        mins = f"{d / 60:.1f}" if d else ""
        notes = r["notes"] if args.long or len(r["notes"]) <= 50 else r["notes"][:47] + "..."
        print(f"{r['recording_id']:<10} {r['camera']:>4} {mins:>5} {r['pass']:>4}  "
              f"{r['route']:<7} {r['annotator']:<10} {r['status']:<11} {r['assigned_on']:<10} "
              f"{r['returned_on']:<10} {notes}")


def cmd_return(args):
    row = manifest.get_assignment(args.recording, args.annotator, args.manifest)
    if row is None:
        sys.exit(f"no assignment for {args.annotator} on {args.recording}")
    f = kit_file(row)
    if not f.exists():
        sys.exit(f"no {f} -- copy it back from Box first")
    bad = problems_in(f)
    if bad:
        sys.exit(f"{f} fails submit_checks.py; send it back:\n  " + "\n  ".join(bad))
    try:
        row = mark_returned(args.recording, args.annotator, args.manifest, on=args.on)
    except (KeyError, ValueError) as e:
        sys.exit(str(e))
    print(f"{args.recording} pass {row['pass']} ({args.annotator}): returned {row['returned_on']}. "
          "That copy is now frozen.")


def cmd_note(args):
    row = manifest.get_assignment(args.recording, args.annotator, args.manifest)
    if row is None:
        sys.exit(f"no assignment for {args.annotator} on {args.recording}")
    manifest.update_assignment(args.recording, args.annotator, args.manifest,
                               notes=append_note(row["notes"], args.text))
    backup(args.manifest)


def cmd_set(args):
    fields = {k: v for k, v in (("status", args.status), ("route", args.route),
                                ("camera", args.camera), ("assigned_on", args.assigned_on),
                                ("returned_on", args.returned_on), ("notes", args.notes))
              if v is not None}
    if args.pass_no is not None:
        fields["pass"] = args.pass_no
    try:
        row = manifest.update_assignment(args.recording, args.annotator, args.manifest, **fields)
    except (KeyError, ValueError) as e:
        sys.exit(str(e))
    backup(args.manifest)
    print(row)


def cmd_add(args):
    if manifest.get_recording(args.recording, path=args.manifest) is None:
        sys.exit(f"recording {args.recording} is not in the manifest")
    try:
        row = manifest.add_assignment(
            args.recording, args.annotator, args.pass_no, args.route, args.manifest,
            camera=args.camera or "", status=args.status, assigned_on=args.assigned_on or "",
            returned_on=args.returned_on or "", notes=args.notes or "")
    except ValueError as e:
        sys.exit(str(e))
    backup(args.manifest)
    print(row)


def cmd_check(args):
    found = audit(args.manifest)
    for line in found:
        print(line)
    print(f"{len(found)} problem(s)" if found else "assignments agree with the kits and gold")
    return 1 if found else 0


def cmd_import(args):
    try:
        n = manifest.import_assignments(args.csv, args.manifest)
    except ValueError as e:
        sys.exit(str(e))
    print(f"imported {n} rows from {args.csv}")


def cmd_export(args):
    print(f"wrote {manifest.export_assignments(args.csv or BACKUP, args.manifest)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(MANIFEST))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="the table, with each video's length")
    p.add_argument("--recording")
    p.add_argument("--long", action="store_true", help="don't shorten notes")
    p.set_defaults(fn=cmd_list)

    def keyed(name, fn, help):
        p = sub.add_parser(name, help=help)
        p.add_argument("recording")
        p.add_argument("annotator")
        p.set_defaults(fn=fn)
        return p

    p = keyed("return", cmd_return, "check a returned file and mark it returned (step 4)")
    p.add_argument("--on", help="returned_on, if not today (YYYY-MM-DD)")
    p = keyed("note", cmd_note, "append to a row's notes")
    p.add_argument("text")
    for name, fn, help in (("set", cmd_set, "change columns on an existing row"),
                           ("add", cmd_add, "add a row no script made")):
        p = keyed(name, fn, help)
        p.add_argument("--pass", dest="pass_no", type=int, choices=(1, 2),
                       required=name == "add")
        p.add_argument("--route", choices=("blind", "seeded"), required=name == "add")
        p.add_argument("--status", choices=manifest.ASSIGNMENT_STATUSES,
                       default="assigned" if name == "add" else None)
        for col in ("camera", "assigned_on", "returned_on", "notes"):
            p.add_argument(f"--{col.replace('_', '-')}")

    sub.add_parser("check", help="compare rows with the kits, gold files and gold table") \
        .set_defaults(fn=cmd_check)
    p = sub.add_parser("import", help="load a CSV into an empty table")
    p.add_argument("csv", nargs="?", default=str(BACKUP))
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("export", help=f"write the table to CSV (default {BACKUP.relative_to(ROOT)})")
    p.add_argument("csv", nargs="?")
    p.set_defaults(fn=cmd_export)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
