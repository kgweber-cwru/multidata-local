#!/usr/bin/env python
"""Add every LearningSpace video recording since a date to the manifest.

    python scripts/ls_roster.py --estimate          # count events, print the request bill, stop
    python scripts/ls_roster.py                     # dry run: crawl, summarize, write nothing
    python scripts/ls_roster.py --apply             # write
    python scripts/ls_roster.py --activity "CSE 2B" --limit 20   # a slice
    python scripts/ls_roster.py --no-link           # minutes only: skip case/learner/preceptor

For each activity with an event since --since (default 2024-07-01): each
event's file list gives every recording, its room, start and length (one call
per event); then the activity's checklist responses give its case x learner
pairs and their faculty observer, and one lookup per pair ties the two
together and finds recordings made outside any event
(multidata.learningspace.crawl_roster). With --limit, linking only ties what
was collected.

A recording already in the manifest (matched by GUID) only has empty fields
filled; anything else becomes a roster row keyed by the GUID. Roster rows
carry GUIDs, not names, except the SP's (the SP checklist's submitter;
the API has no SP GUID). Duration is the recording's longest file.

Names are never printed. Requests are paced and cached (data/ls_api/cache),
so a dry run followed by --apply asks the server once. Back up
manifest.sqlite before the first --apply.
"""
import argparse
import sys
from datetime import datetime, timezone

from multidata import learningspace as ls
from multidata import manifest
from multidata.paths import MANIFEST_PATH


def preview(existing, fields):
    """What manifest.upsert_ls_recording would do, without writing:
    (status, conflicts). Mirrors its fill-empty-only rule."""
    if existing is None:
        return "added", []
    fill, conflicts = False, []
    for col, new in fields.items():
        if new in (None, ""):
            continue
        old = existing.get(col)
        if old in (None, ""):
            fill = True
        elif str(old) != str(new) and not (isinstance(new, float) and old is not None
                                           and abs(float(old) - new) < 1e-6):
            conflicts.append((col, old, new))
    return ("filled" if fill else "unchanged"), conflicts


def same_title(col, ours, theirs):
    """The export and the API spell titles with different spacing: not a conflict."""
    return col.endswith("_title") and ls._norm(str(ours)) == ls._norm(str(theirs))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default=ls.ROSTER_SINCE, help="YYYY-MM-DD (event local date)")
    ap.add_argument("--activity", action="append",
                    help="limit to activities whose title contains this, or this GUID")
    ap.add_argument("--limit", type=int, help="stop after this many recordings")
    ap.add_argument("--no-link", action="store_true",
                    help="skip responses and pair lookups (no case, learner, preceptor)")
    ap.add_argument("--estimate", action="store_true",
                    help="fetch activities and events only, print the request estimate")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--refresh", action="store_true", help="ignore the response cache")
    ap.add_argument("--pace", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    args = ap.parse_args(argv)

    client = ls.Client.from_env(pace=args.pace, refresh=args.refresh)
    acts = ls.activities_since(client.activities(), args.since)
    if args.activity:
        acts = [a for a in acts if any(ls.match_activity(a, w) for w in args.activity)]
    acts.sort(key=lambda a: a.get("StartDate") or "")
    print(f"{len(acts)} activities may hold events since {args.since}")
    if not acts:
        return 0

    if args.estimate:
        n_ev = with_ev = 0
        for a in acts:
            evs = ls.events_since(client.events(a["ID"]), args.since)
            n_ev += len(evs)
            with_ev += bool(evs)
        print(f"{with_ev} activities have {n_ev} events since {args.since}.")
        print(f"Requests beyond these: {n_ev}+ for event files (one per 1000 files), "
              f"{with_ev}+ for responses (one per 1000 rows; often 5-25)"
              + ("" if args.no_link else
                 ", and one per case x learner pair for linking"))
        print(f"Network requests made: {client.calls}")
        return 0

    rooms = manifest.all_ls_rooms(args.manifest)
    roster = ls.crawl_roster(client, acts, since=args.since, limit=args.limit,
                             link=not args.no_link, rooms=rooms, log=print)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    existing = {r.ls_recording_id: vars(r)
                for r in manifest.all_recordings(args.manifest, roster=True) if r.ls_recording_id}
    status, conflicts = {}, []
    for guid, fields in roster.rows.items():
        bad = set(fields) & ls.ROSTER_NAME_FIELDS
        if bad:   # never: crawl_roster sets no names but the SP's; keep it that way
            sys.exit(f"refusing: name fields {sorted(bad)} in a roster row")
        if args.apply:
            st, _rid, conf = manifest.upsert_ls_recording({**fields, "ls_synced_at": now},
                                                          path=args.manifest)
        else:
            st, conf = preview(existing.get(guid), fields)
        status[st] = status.get(st, 0) + 1
        conflicts += [(guid, *c) for c in conf if not same_title(*c)]

    rows = list(roster.rows.values())
    secs = [r["duration_s"] for r in rows if r.get("duration_s") is not None]
    preceptors = {r["ls_preceptor_id"] for r in rows if r.get("ls_preceptor_id")}
    c = roster.counts
    print(f"\n{'APPLY' if args.apply else 'dry run'} summary")
    print(f"  activities read {c.get('activities', 0)}, with events {c.get('activities with events', 0)}; "
          f"events {c.get('events', 0)}")
    print(f"  recordings {len(rows)}: " + ", ".join(f"{k} {v}" for k, v in sorted(status.items())))
    print(f"  minutes {sum(secs) / 60:,.0f} over {len(secs)} recordings with a length; "
          f"{len(rows) - len(secs)} without")
    if not args.no_link:
        linked = sum(1 for r in rows if r.get("ls_case_id"))
        print(f"  case x learner pairs {c.get('pairs', 0)}, pair lookups {c.get('pair lookups', 0)}; "
              f"recordings linked {linked}, unlinked {c.get('unlinked', 0)}, "
              f"found only through pairs (outside events) {c.get('recordings outside events', 0)}")
        print(f"  distinct preceptors {len(preceptors)} "
              f"(recordings with one: {sum(1 for r in rows if r.get('ls_preceptor_id'))})")
        sps = {r["sp_name"] for r in rows if r.get("sp_name")}
        print(f"  distinct SPs {len(sps)} "
              f"(recordings with one: {sum(1 for r in rows if r.get('sp_name'))})")
    print(f"  network requests {client.calls}")
    for guid, col, ours, theirs in conflicts[:20]:
        print(f"  CONFLICT {guid[:8]} {col}: ours {ls.describe(col, ours)!r}, "
              f"LearningSpace {ls.describe(col, theirs)!r}")
    if len(conflicts) > 20:
        print(f"  ... {len(conflicts) - 20} more conflicts")
    for w in roster.warnings[:20]:
        print(f"  warning: {w}")
    if len(roster.warnings) > 20:
        print(f"  ... {len(roster.warnings) - 20} more warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
