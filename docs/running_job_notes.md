# Notes for checking on the running pose job

> If you're reading this because something happened to Kate: yes, too damn bad
> — but the pending rows don't know that, and neither does `pose_status`. The
> 3070 already took the omen hit for this project; the job itself is fine.
> Read `multidata_local_pipeline.md` §9 for the architecture, then come back
> here for the actual commands. Press on.

Two sharded workers, splitting the pending rows so neither duplicates the
other's work (`--shard 0/2` / `--shard 1/2`, interleaved over the
duration-sorted list so each gets a short+long mix, not "all the short ones").

PIDs: shard 0 = 1921, shard 1 = 1922 (`logs/pose_job_shard{0,1}.pid`).

```bash
cd ~/projects/multidata-local

# still alive? (both PIDs at once)
ps -p "$(cat logs/pose_job_shard0.pid),$(cat logs/pose_job_shard1.pid)" -o pid,etime,%cpu,command

# clean structured progress -- both workers interleaved, timestamped INFO lines
tail -f logs/run_stage.log

# raw per-worker output incl. tqdm live frame-rate + onnxruntime/objc noise
tail -f logs/nohup_pose_shard0.out
tail -f logs/nohup_pose_shard1.out

# done/pending/failed tally across both workers
sqlite3 manifest.sqlite "select pose_status, count(*) from videos group by pose_status;"

# to stop everything
kill "$(cat logs/pose_job_shard0.pid)" "$(cat logs/pose_job_shard1.pid)"
```

If you need to restart both workers from scratch (e.g. after a code change),
completed rows stay `done` in the manifest either way, so a restart just picks
up wherever `pending` rows are left -- no need to clear `pose_status` again
unless you're intentionally re-running already-`done` rows (e.g. a model
change, like the Body->Wholebody switch).

To add a third worker later, all three need relaunching with `--shard i/3`
(0/3, 1/3, 2/3) -- shard counts aren't mix-and-matchable mid-run; every active
worker needs the same total or their slices won't stay disjoint.
