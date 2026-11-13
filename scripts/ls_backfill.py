#!/usr/bin/env python
"""Link manifest recordings to LearningSpace and fill in what it knows.

    python scripts/ls_backfill.py                 # dry run: every recording with video
    python scripts/ls_backfill.py --recording 261505   # one or more recordings
    python scripts/ls_backfill.py --apply         # write the changes
    python scripts/ls_backfill.py --prune         # also drop recordings with no video

For each recording: finds it in LearningSpace (multidata.learningspace),
checks its files against ours by filename, and fills the empty `ls_*`
columns, `recording_start_utc`, `preceptor_name` (the faculty observer), the
videos' file links, the `recording_scores` table and the `ls_rooms` map. An
existing value that differs is reported as a conflict and never overwritten.

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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recording", action="append", help="limit to these recording_ids")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--prune", action="store_true", help="drop recordings with no video")
    ap.add_argument("--refresh", action="store_true", help="ignore the response cache")
    ap.add_argument("--pace", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    args = ap.parse_args(argv)
    path = args.manifest
    mode = "APPLY" if args.apply else "dry run"

    videos = {}
    for v in manifest.all_videos(path):
        videos.setdefault(v.recording_id, []).append(vars(v))
    recordings = {c.recording_id: vars(c) for c in manifest.all_recordings(path)}
    ids = args.recording or sorted(videos)
    missing = [i for i in ids if i not in recordings]
    if missing:
        sys.exit(f"not in the manifest: {', '.join(missing)}")

    client = ls.Client.from_env(pace=args.pace, refresh=args.refresh)
    lookups = ls.Lookups(client)
    failed, partial, conflicted, changed = [], [], [], 0
    print(f"{mode}: {len(ids)} recordings")
    for cid in ids:
        recording, vids = recordings[cid], videos.get(cid, [])
        res = ls.resolve(recording, vids, client, lookups)
        if not res.ok:
            failed.append(cid)
            print(f"  {cid}  NOT LINKED: {'; '.join(res.problems)}")
            continue
        plan = ls.plan_updates(recording, vids, res)
        parts = [f"{len(plan.recording_set)} recording fields",
                 f"{sum(len(s) for s in plan.video_set.values())} file fields",
                 f"{len(res.scores)} score rows"]
        state = "linked" if res.linked else "PARTLY linked"
        print(f"  {cid}  {state} ({', '.join(parts)})")
        for col, val in plan.recording_set.items():
            if col in ls.NAME_FIELDS:
                print(f"      + {col} {ls.describe(col, val)}")
        for where, col, old, new in plan.conflicts:
            print(f"      ! {where} {col}: ours {ls.describe(col, old)!r}, "
                  f"LearningSpace {ls.describe(col, new)!r} (kept ours)")
        for w in res.warnings:
            print(f"      ~ {w}")
        if res.extra_files:
            print(f"      ~ {len(res.extra_files)} more file(s) in LearningSpace than on disk")
        if plan.conflicts:
            conflicted.append(cid)
        if not res.linked:
            partial.append(cid)
        if args.apply:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            manifest.update_recording(cid, path, **plan.recording_set, ls_synced_at=now)
            for cam, fields in plan.video_set.items():
                manifest.update(cid, cam, path, **fields)
            manifest.set_recording_scores(cid, res.scores, path)
            if res.room:
                clash = manifest.set_ls_room(*res.room, path=path)
                if clash is not None:
                    print(f"      ! room GUID already mapped to room {clash}, "
                          f"this recording says {res.room[1]}")
            changed += 1

    print(f"\n{len(ids) - len(failed) - len(partial)} linked, {len(partial)} partly linked "
          f"(no recording), {len(failed)} not linked, "
          f"{len(conflicted)} with conflicts; {client.calls} requests made")
    if args.apply:
        print(f"wrote {changed} recordings")
    if args.prune:
        gone = manifest.prune_recordings_without_videos(path, dry_run=not args.apply)
        verb = "dropped" if args.apply else "would drop"
        print(f"prune: {verb} {len(gone)} recordings with no video")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
