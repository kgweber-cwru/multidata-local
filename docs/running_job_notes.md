# Notes for checking on the running pose job

Read `multidata_local_pipeline.md` §9 for the architecture, then come back
here for the actual commands.

**Current run (started 2026-09-04 13:05, `tofino`, RTX 5080):** single
worker, no sharding -- only 18 pending rows left, not worth splitting across
workers. `--profile` is on, so `logs/run_stage.log` gets a decode/detect/pose
timing line per video; keep an eye on those to track how processing time
moves as the GPU/driver/onnxruntime stack changes over time, not just whether
the batch finished.

```bash
cd ~/projects/multidata-local

conda activate md-pose && python scripts/run_stage.py pose --accel-device cuda --profile \
  > logs/nohup_pose.out 2>&1 &
echo $! > logs/pose_job.pid   # verify with `ps -p $(cat logs/pose_job.pid)` -- nohup's
                              # own `&` can hand back the wrong $! if you're piping the
                              # launch command through another wrapper shell; confirm the
                              # PID's COMMAND column actually says run_stage.py before
                              # trusting the .pid file
```

PID: `138126` (`logs/pose_job.pid`).

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

# done/pending/failed tally -- sqlite3 CLI isn't installed on this box, use python's
# stdlib sqlite3 module instead
python3 -c "
import sqlite3
conn = sqlite3.connect('manifest.sqlite')
for row in conn.execute('select pose_status, count(*) from videos group by pose_status'):
    print(row)
"

# to stop it
kill "$(cat logs/pose_job.pid)"
```

18 rows pending at launch, ~17.3 hours of total video content. At the
measured ~1.98x realtime (5080, `--profile` smoke test, 2026-09-04 -- see
`multidata_local_pipeline.md` §9), that's **very roughly 8.5-9 hours**
wall clock, i.e. expect it done sometime around 2026-09-04 21:30-22:00 --
not a committed number, just a planning estimate; the pending list mixes
~30-70 minute encounters and per-video overhead (model load, detector
warmup) isn't free, so actual time will drift from this.

If you need to restart (e.g. after a code change), completed rows stay
`done` in the manifest either way, so a restart just picks up wherever
`pending` rows are left -- no need to clear `pose_status` again unless
you're intentionally re-running already-`done` rows (e.g. a model change,
like the Body->Wholebody switch).

If you add a second worker later, both need `--shard i/2` (`0/2`/`1/2`,
interleaved over the duration-sorted list so each gets a short+long mix,
not "all the short ones") -- `manifest.pending()` has no claim/lock
mechanism, so two unsharded workers would duplicate each other's work.
