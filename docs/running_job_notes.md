# Notes for launching and checking on jobs on tofino

Everything runs on `tofino` now — it is the only machine that processes and
the only one that writes `manifest.sqlite` (see
[current_status.md](current_status.md), "Machines, data and backups"). Read
`multidata_local_pipeline.md` §9 for the pose architecture and throughput
baseline, then come back here for the actual commands.

> **Pose is parked as of 2026-09-14** as a research direction (see
> **[pose_parked.md](pose_parked.md)**), but the stage still runs on newly
> ingested video so outputs don't fall behind. The output-format change that
> note asked for before any new batch is done: pose writes float16 `.npz`
> since 2026-10-26.

**As of 2026-10-26 evening a pose job is running** over the 8 newly
ingested Room 6 videos from 2026-02-16 (cases 260706, 260730, 260754,
260778; cameras 4 and 14). Their audio/ASR/ELAN are already done on camera 4
(camera 14 is `skipped` for those stages, as the second camera of each case
always is). Before that: 2026-09-04 13:05 -> 23:30 on `tofino` (RTX 5080), 18
rows, zero errors, zero fallback; manifest at `60 done, 1 failed` (the
failed one is a real content issue — "no frames, or nobody detected" — not a
device problem). Full throughput numbers (1.67x realtime aggregate,
detect/pose split, etc.) are recorded in `multidata_local_pipeline.md` §9 as
the settled RTX 5080 baseline — update that doc, not this one, when a future
batch changes the numbers enough to matter.

One estimation lesson from that run: the pre-launch ETA (~8.5-9h) was built
off a single `--profile` smoke-test clip's realtime factor (1.98x) and
undershot the actual wall clock (10h25m, 1.67x aggregate) by about 20%.
A single short clip's ratio runs faster than a mixed real batch — use the
in-progress aggregate (total processed duration / total wall clock so far)
for a better mid-run estimate next time, not a pre-batch smoke test alone.

## Launching a new batch

```bash
cd ~/projects/multidata-local

conda activate md-pose && nohup python scripts/run_stage.py pose --accel-device cuda --profile \
  > logs/nohup_pose.out 2>&1 &
echo $! > logs/pose_job.pid   # verify with `ps -p $(cat logs/pose_job.pid)` -- nohup's
                              # own `&` can hand back the wrong $! if you're piping the
                              # launch command through another wrapper shell; confirm the
                              # PID's COMMAND column actually says run_stage.py before
                              # trusting the .pid file
disown                        # so the launching shell exiting doesn't take the job with it
```

`--profile` is worth leaving on for every run, not just spot checks: it costs
nothing but a few `time.perf_counter()` calls, and gives a per-video
decode/detect/pose split in `logs/run_stage.log` for tracking how processing
time moves as the GPU/driver/onnxruntime stack changes over time — that's
what produced the current §9 baseline.

## Checking on it

```bash
cd ~/projects/multidata-local

# still alive?
ps -p "$(cat logs/pose_job.pid)" -o pid,etime,%cpu,command

# is it actually on the GPU?
nvidia-smi --query-compute-apps=pid,used_memory --format=csv | grep "$(cat logs/pose_job.pid)"

# clean structured progress, timestamped INFO lines + per-video profile split
tail -f logs/run_stage.log

# raw output incl. tqdm live frame-rate + onnxruntime provider-assignment noise
tail -f logs/nohup_pose.out

# done/pending/failed tally (the sqlite3 CLI is installed on tofino since its rebuild)
sqlite3 manifest.sqlite "select pose_status, count(*) from videos group by pose_status"

# to stop it
kill "$(cat logs/pose_job.pid)"
```

If you need to restart (e.g. after a code change), completed rows stay
`done` in the manifest either way, so a restart just picks up wherever
`pending` rows are left -- no need to clear `pose_status` again unless
you're intentionally re-running already-`done` rows (e.g. a model change,
like the Body->Wholebody switch).

If you add a second worker later, both need `--shard i/2` (`0/2`/`1/2`,
interleaved over the duration-sorted list so each gets a short+long mix,
not "all the short ones") -- `manifest.pending()` has no claim/lock
mechanism, so two unsharded workers would duplicate each other's work.

## Nightly backup to the `/wddata` array

`scripts/backup_to_raid.sh` runs from Kate's crontab on tofino at 02:30
(`crontab -l` to see the entry). It writes to `/wddata/multidata-backup/`:

| Path | What |
|---|---|
| `current/` | rsync mirror of the whole checkout, `.git` and `data/raw` included; `.env` and the live `manifest.sqlite` excluded |
| `changed/<date>/` | files that night's run deleted or overwrote in `current/` — an accidental `rm` on tofino is recoverable from here |
| `manifest/manifest_<date>.sqlite` | consistent `sqlite3 .backup` snapshot, integrity-checked before it's kept |
| `LAST_SUCCESS` | timestamp of the last run that finished |

`changed/` and `manifest/` keep 60 days. The script skips (and logs why) if
`/wddata` isn't mounted — fstab mounts it `nofail`, so without that check a
missing array would quietly fill the root SSD — or if the previous night's
run still holds the lock.

```bash
cd ~/projects/multidata-local

cat /wddata/multidata-backup/LAST_SUCCESS    # should be within the last day
tail logs/backup.log                         # start/done lines + rsync totals
scripts/backup_to_raid.sh                    # run one now (safe alongside a job)

# restore a file, or a whole directory, from the mirror or a dated change set
cp -p /wddata/multidata-backup/current/data/gold/261456/261456.eaf data/gold/261456/
ls /wddata/multidata-backup/changed/

# restore the manifest -- stop every job first; this replaces the live database
cp /wddata/multidata-backup/manifest/manifest_<date>.sqlite manifest.sqlite
```

**`/wddata` is NTFS** (ntfs-3g, shown as `fuseblk`). It has no Unix owners or
permission bits: everything on it shows as `root:root` mode `0777`, and
`chown`/`chmod` are silent no-ops. That's expected, not a fault, and it's
why the script uses `rsync -rlt` rather than `-a`. The upside is that the
array can be read on another machine if tofino itself dies.

**It is slow: ~5 MB/s writes** (measured 2026-10-26 with plain `dd`, so not
rsync's doing; the SSD does 341 MB/s). It's a WD My Book Duo on USB 3,
attached with the `usb-storage` driver and mounted through ntfs-3g (FUSE),
and there are no USB or disk errors in the kernel log. Nightly runs only
copy what changed, so this mostly matters for a first full mirror: the
~21 GB checkout took about an hour, and the full corpus (~0.5 TB) would take
about a day. If it needs to be faster, try mounting with the in-kernel `ntfs3`
driver instead of ntfs-3g (an fstab change, needs sudo), and check the
enclosure isn't busy resyncing its RAID mirror.

What this doesn't cover: losing tofino and the array together (fire, theft,
power surge). Code is also on GitHub and raw video can be re-exported from
the recording system on demand. The irreplaceable part is the manifest and
the annotation/gold `.eaf` files (the exported `benchmarks/references/` text
is in git, the `.eaf`s are not). Their only current copies are tofino and
this array — the MBA holds a copy from the 2026-10-26 merge that nothing
keeps up to date.
