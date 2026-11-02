# Operations: running jobs on the processing host

How to start long jobs so they survive logging out, check on them, stop
them, and look after the backups. Everything runs on the processing host
(during the pilot, the workstation `tofino`), from
`~/projects/multidata-local`; the backup section is specific to tofino. How the stages themselves work is in
[pipeline.md](pipeline.md).

---

## Running a stage

```bash
cd ~/projects/multidata-local
conda activate md-speech          # or md-pose for pose / views
python scripts/run_stage.py <stage> [--only <case_id>]
```

Each stage works only on rows still `pending` for it, so re-running is
safe: a killed job just picks up where it stopped. Short stages (audio,
asr, elan, prosody, sync, views) can run in the foreground. A wrong env stops
with one message ("can't import its module here ... run it in the md-pose
env").

## Long jobs: start them detached

Pose takes the GPU for hours (~35 min per case). Start it so it survives the
terminal closing, and record the worker PIDs:

```bash
cd ~/projects/multidata-local
for i in 0 1; do
  setsid nohup ~/miniconda3/envs/md-pose/bin/python scripts/run_stage.py pose \
      --accel-device cuda --shard $i/2 >/dev/null 2>&1 < /dev/null &
done
sleep 5
ps -eo pid,args | awk '$2 ~ /md-pose\/bin\/python$/ && /run_stage.py pose/ {print $1}' > logs/pose.pid
cat logs/pose.pid
```

- **Two workers** (`--shard 0/2`, `--shard 1/2`) get ~25% more out of the
  RTX 5080 than one. The shards split the pending list; unsharded workers
  would duplicate each other's work, because rows aren't locked.
- **Workers take their list of videos when they start.** Videos registered
  later aren't picked up; run pose again after the batch.
- **Calling the env's Python directly** (as above) needs no `conda activate`;
  ffmpeg is found in the same env.
- Everything goes to `logs/run_stage.log`, so the job's own output can go to
  `/dev/null`.

To run something after a job finishes (a test, another stage), a small
script in `logs/` that waits on the PIDs works well:
`while kill -0 $(cat logs/pose.pid) 2>/dev/null; do sleep 60; done`, then the
next command. `logs/after_pose_batch.sh` is an example.

## Checking on a job

```bash
cd ~/projects/multidata-local
ps -o pid,etime,%cpu,args -p "$(paste -sd, logs/pose.pid)"      # still alive?
nvidia-smi                                                       # on the GPU?
tail -f logs/run_stage.log                                       # progress
sqlite3 manifest.sqlite "select pose_status, count(*) from videos group by 1"
sqlite3 manifest.sqlite "select stage, status, case_id, camera, round(wall_s), round(realtime_x,2)
                         from stage_runs order by id desc limit 10"
```

Each finished video logs its speed ("pose 261498/20: 1100 s wall, 1.10x
realtime") and adds a `stage_runs` row. For an ETA mid-batch, use the batch's
own aggregate (media hours done / wall hours so far), not a single clip.

## Stopping and redoing

```bash
kill $(cat logs/pose.pid)          # stop; finished rows stay done
```

The video in progress isn't saved (pose writes each file when it finishes),
so it stays `pending`. To redo rows that are `done` or `failed` (after a
model change, or once the cause of a failure is fixed), set them back to
`pending`:

```bash
sqlite3 manifest.sqlite "update videos set pose_status='pending' where pose_status='failed'"
```

Why a row failed is in `logs/run_stage.log` (one `FAILED` line, full
traceback below it) and in `stage_runs.error`.

---

## Nightly backup to the `/wddata` array

`scripts/backup_to_raid.sh` runs from Kate's crontab at 02:30
(`crontab -l`). It writes to `/wddata/multidata-backup/`:

| Path | What |
|---|---|
| `current/` | rsync mirror of the whole checkout, `.git` and `data/raw` included; `.env` and the live `manifest.sqlite` excluded |
| `changed/<date>/` | files that night's run deleted or overwrote in `current/`: an accidental `rm` on tofino is recoverable from here for 60 days |
| `manifest/manifest_<date>.sqlite` | integrity-checked `sqlite3 .backup` snapshot, 60 days |
| `LAST_SUCCESS` | timestamp of the last run that finished |

The script skips (and logs why) if `/wddata` isn't mounted, so a missing
array can't quietly fill the main drive, or if last night's run is still
going.

```bash
cat /wddata/multidata-backup/LAST_SUCCESS    # should be within the last day
tail logs/backup.log
scripts/backup_to_raid.sh                    # run one now (safe alongside a job)

# restore a file or folder, from the mirror or a dated change set
cp -p /wddata/multidata-backup/current/data/gold/<case>/<file> data/gold/<case>/
ls /wddata/multidata-backup/changed/

# restore the manifest: stop every job first; this replaces the live database
cp /wddata/multidata-backup/manifest/manifest_<date>.sqlite manifest.sqlite
```

- **`/wddata` is NTFS** (ntfs-3g): no Unix owners or permissions, everything
  shows `root:root 0777`, `chown` does nothing. Expected; it's why the script
  uses `rsync -rlt`. The upside: the array can be read on another machine.
- **It's slow, ~5 MB/s writes** (USB 3 WD My Book Duo through ntfs-3g).
  Nightly runs copy only changes; a first full mirror of the corpus would take
  about a day. The in-kernel `ntfs3` driver might be faster (fstab change).
- **Not covered:** losing tofino and the array together. Code is on GitHub
  and raw video can be re-exported from the recording system; the manifest
  and the annotation/gold `.eaf` files exist only on tofino and this array.
