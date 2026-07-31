# Notes for checking on the running pose job

PID is 99885

```bash
cd ~/projects/multidata-local
ps -p $(cat logs/pose_job.pid)          # still alive?
tail -f logs/run_stage.log              # clean structured progress (INFO lines, profile if you add --profile)
tail -f logs/nohup_pose.out             # raw output incl. tqdm live frame-rate + onnxruntime noise
sqlite3 manifest.sqlite "select pose_status, count(*) from videos group by pose_status;"  # done/pending/failed tally
```