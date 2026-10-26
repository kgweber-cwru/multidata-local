# Pose — parked 2026-09-14

**Read this before touching anything pose-related.** Pose *generation* works
and is finished for the current sample. Pose *usability* is barely started, and
there is one specific gap that blocks everything downstream. This note exists so
that coming back cold costs a read, not a re-derivation.

Parked because the priority is audio transcription validation (gold references
and the ASR bake-off). Nothing about pose is broken or waiting on a fix — it is
simply not the thing being worked on.

Operational commands (launch, check, stop) stay in
[running_job_notes.md](running_job_notes.md). Architecture and the throughput
baseline stay in [../multidata_local_pipeline.md](../multidata_local_pipeline.md)
§9. This file is the part neither of those covers: **what state the work is in,
and what to do next.**

---

## Where we got

| | |
|---|---|
| Videos processed | **60 done, 1 failed** of the original 61; 8 more (Room 6, 2026-02-16) ingested and running 2026-10-26 |
| Total material | 32.9 h, mean 32.3 min per video, ~2 cameras per case (original 61) |
| Model | `rtmlib.Wholebody`, 133 keypoints (COCO-WholeBody) |
| Runs on | `tofino` (RTX 5080, `cuda`). The Mac (`mps`) path still works in the code, but the Mac mini is being retired |
| Throughput | **1.67x realtime** aggregate on the 5080, at `--detect-every 1` |
| Output | `data/pose/<case_id>/<camera>.npz`, float16 compressed — 1.6 GB for the 60 (~27 MB each; was 23 GB as float32 `.pkl`) |

The RTX 5080 is tofino's permanent card (the original 3070 failed in 2026-07 and
is gone). That baseline is settled — **don't re-benchmark it.** The CUDA detector
routing and the `preload_dlls()` fix that were once uncommitted on tofino are
merged into `main` and confirmed against a full 18-video batch.

Also settled, don't redo: the `Body` → `Wholebody` switch, and the
`onnxruntime-gpu` force-reinstall gotcha for fresh `md-pose` envs on Linux
(documented at the top of `env/pose-nvidia.yml` and in the README quick start).

**Where the current 60 files came from (2026-10-26).** When the machines were
consolidated, only 18 of tofino's 60 `.pkl` files were Wholebody (the Sep 4
CUDA batch); the other 42 were left over from the earlier `Body` run, 17
keypoints. The Mac mini's 60 (Jul 31/Aug 1, `mps`) were all Wholebody, so
those are the set that was converted to `.npz` and now lives on tofino;
tofino's old 60 are archived in `~/multidata-archive/2026-10-26_pre-merge/pose/`.
So the existing 60 are a Mac run and anything new is a CUDA run. On the one
clip compared (260656/23), the two agree to a median of 1.0 px (95th
percentile 4.1 px) on confident keypoints — backend numerics, not a model
difference.

---

## The one thing that actually blocks usability

**Pose output has no person identity.** This is the gap, and it is easy to miss
because the data looks complete.

`entry["keypoint"]` has shape `(M, T, V, C)` — M detection slots, T frames, V
keypoints, C coordinates. **M is a slot, not a person.** Slot 0 in frame 900 and
slot 0 in frame 901 are "the first thing the detector happened to return in that
frame," which may well be two different humans. The tracker is off, deliberately
and from the start.

Two consequences:

1. **Nothing can be attributed to a role.** "Did the learner lean forward while
   the patient was speaking" is unanswerable, because no slot is reliably the
   learner. Tying pose to the speaker tiers — which is the whole point of having
   pose alongside transcripts — needs person identity first.
2. **`kinematics.py` is built on the assumption it doesn't have.** It reads one
   slot (`entry["keypoint"][s]`) as though that slot were one person over time.
   That's why its docstring says "exploratory, not validated" — the 63 lines are
   a notebook sketch with eyeballed thresholds, not a component. Don't trust a
   number that comes out of it today, and don't tune its thresholds before
   identity is solved: you would be tuning against noise.

**When you return, this is the first real problem, not the format question.**
Two plausible routes, neither investigated:

- Turn on a tracker (rtmlib's own, or ByteTrack / BoT-SORT alongside the
  detector) so slots become stable tracks.
- Associate after the fact, by keypoint proximity between consecutive frames.
  Cheaper to prototype against the 60 videos already on disk, since it needs no
  re-run.

A second, harder identity problem sits behind it: **the same person across two
cameras.** Leave it alone until single-camera identity works.

---

## What to do when you come back, in order

1. ~~Change the output format before running any new batch.~~ **Done
   2026-10-26, as float16** (Kate's call: 0.5 px is fine for this data).
   `pose.save()` writes `<camera>.npz` via `np.savez_compressed` with float16
   arrays; `pose.load()` reads that or a legacy `.pkl` and always returns
   float32, so nothing downstream does float16 math.
   `scripts/sample_pose_overlays.py` reads through `pose.load()`;
   `kinematics.py` takes an already-loaded entry and needed no change. The 60
   existing files were converted, not re-run, with `scripts/convert_pose.py`
   (22.9 GB -> 1.6 GB, 14x), which checks each `.npz` against its `.pkl`
   before anything is deleted. The bound is 0.5 px for coordinates under
   2048 px, i.e. anywhere inside a 1920-wide frame; camera 37 records
   1920x1080 and has a few off-frame, low-confidence extrapolations past
   2048 px, where float16 rounds to the nearest 2 px, so the check scales
   with magnitude there.

2. **Solve person identity** — the section above. Prototype against the existing
   60 videos; no re-run needed.

3. **Only then revisit `kinematics.py`**, and validate it against something real
   rather than eyeballing thresholds.

4. **Try `--detect-every` on CUDA.** The 1.67x baseline was measured at the
   default stride of 1 — every frame detected. Detection is still ~60% of wall
   clock on the 5080, so striding should still pay, but **the 2.8–4x speedup in
   the docs was measured on the Mac with a CPU-pinned detector and has never been
   tried on CUDA.** Sanity-check `render_overlay()` at the chosen stride on a clip
   with real movement before trusting it for a batch — a held bounding box lags a
   fast mover.

5. **Look at the one failed video**: case `260656`, camera `26`, a 129-second
   clip. Failure was "no frames, or nobody detected" — a content issue, not a
   device problem. Look at the clip before suspecting the code.

---

## Infrastructure that was going to come back with pose

Both were deferred *with* pose, because pose was what made them necessary.
**Both are now settled the simple way** (2026-10-26): tofino is the only
machine that processes, so there is nothing to keep in sync.

- **One copy of the files.** The Mac mini and tofino used to keep their own
  `data/`, and they drifted. Instead of a shared network disk, everything was
  merged onto tofino, and tofino backs itself up nightly to its `/wddata`
  array — see [current_status.md](current_status.md), "Machines, data and
  backups".
- **One copy of the state.** `manifest.sqlite` forked whenever both boxes
  wrote `pose_status`. With one writer it can't. Postgres is only needed again
  if a second machine has to write — and `--shard i/n` stays the way to run
  several workers on tofino itself.

Sizing, for whenever the corpus grows: as float16 `.npz`, pose output is
~27 MB per video against ~143 MB of source video, down from ~383 MB as
float32 `.pkl`. It is no longer the big storage class — raw video is.

---

## Small things that will bite

- **`torch` is not installed in either pose env**, so
  `device.best_torch_device()`'s auto-detect never actually runs. Always pass
  `--accel-device cuda` or `mps` explicitly. The auto-detect path reads like it
  works; it doesn't.
- **`data/pose_overlays/` is 5.7 GB of sample renders**, produced by
  `scripts/sample_pose_overlays.py` for eyeballing. Not an output anything
  depends on — safe to delete and regenerate. They were never copied to tofino.
- **Case 261573 (both cameras) is a long recording of an empty room.** Its pose
  files have someone detected in 0.1–0.3% of frames, and compress to almost
  nothing. That's the video, not a bug.
- **The detector is CPU-pinned on `mps` only**, because CoreML crashes at
  inference on YOLOX's dynamic NMS output shape. On CUDA it runs on the GPU.
  That asymmetry is intentional and already correct in the code.
