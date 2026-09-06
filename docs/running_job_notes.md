# Notes for launching and checking on the pose job

Read `multidata_local_pipeline.md` §9 for the architecture and the current
throughput baseline, then come back here for the actual commands.

**No job is running right now.** Last run: 2026-09-04 13:05 -> 23:30 on
`tofino` (RTX 5080), all 18 pending rows, zero errors, zero fallback.
Manifest is at `60 done, 1 failed` (the failed one is a real content issue —
"no frames, or nobody detected" — not a device problem) with nothing
`pending`. Full throughput numbers (1.67x realtime aggregate, detect/pose
split, etc.) are recorded in `multidata_local_pipeline.md` §9 as the settled
RTX 5080 baseline — update that doc, not this one, when a future batch
changes the numbers enough to matter.

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

If you need to restart (e.g. after a code change), completed rows stay
`done` in the manifest either way, so a restart just picks up wherever
`pending` rows are left -- no need to clear `pose_status` again unless
you're intentionally re-running already-`done` rows (e.g. a model change,
like the Body->Wholebody switch).

If you add a second worker later, both need `--shard i/2` (`0/2`/`1/2`,
interleaved over the duration-sorted list so each gets a short+long mix,
not "all the short ones") -- `manifest.pending()` has no claim/lock
mechanism, so two unsharded workers would duplicate each other's work.
