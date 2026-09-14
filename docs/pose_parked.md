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
| Videos processed | **60 done, 1 failed** of 61 — nothing pending |
| Total material | 32.9 h, mean 32.3 min per video, ~2 cameras per case |
| Model | `rtmlib.Wholebody`, 133 keypoints (COCO-WholeBody) |
| Runs on | Mac (`mps`) and `tofino` (RTX 5080, `cuda`) — both exercised |
| Throughput | **1.67x realtime** aggregate on the 5080, at `--detect-every 1` |
| Output | `data/pose/<case_id>/<camera>.pkl` — 23 GB for 60 videos, ~383 MB each |

The RTX 5080 is tofino's permanent card (the original 3070 failed in 2026-07 and
is gone). That baseline is settled — **don't re-benchmark it.** The CUDA detector
routing and the `preload_dlls()` fix that were once uncommitted on tofino are
merged into `main` and confirmed against a full 18-video batch.

Also settled, don't redo: the `Body` → `Wholebody` switch, and the
`onnxruntime-gpu` force-reinstall gotcha for fresh `md-pose` envs on Linux
(documented at the top of `env/pose-nvidia.yml` and in the README quick start).

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

1. **Change the output format before running any new batch.** Measured on
   `data/pose/252850/4.pkl` (139.4 MB):

   | | Size | |
   |---|---|---|
   | pickle, float32 (current) | 139.4 MB | |
   | float32 + `np.savez_compressed` | **28.0 MB** | **5.0x, lossless** |
   | float16 + `np.savez_compressed` | 14.7 MB | 9.5x, but 0.5 px coordinate error |

   The arrays are float32 and 53% zero-padding (unused detection slots), which
   is where the compression comes from. Writer is
   [pose.py:190](../src/multidata/pose.py#L190). Take the lossless 5x; float16's
   extra 2x costs 0.5 px, which is probably fine for kinematics but is a real
   decision rather than free.

   Do it **first** so a new batch doesn't generate the waste twice — and note it
   touches readers too (`kinematics.py`, `scripts/sample_pose_overlays.py`), and
   the 60 existing `.pkl` files need either a conversion pass or a reader that
   accepts both.

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

## Infrastructure that comes back with pose

Both were deliberately deferred *with* pose, because pose is what makes them
necessary. Neither is needed for transcription validation.

- **One copy of the files.** The mini and tofino each keep their own `data/`
  today, which is the "two boxes out of sync" problem. The answer is the 1 TB
  shared disk on the private network, mounted at the same path on both machines,
  so there is one copy rather than two that drift. Pose is the workload that
  makes this matter — it is both the biggest consumer of raw video and the
  biggest producer of output.
- **One copy of the state.** `manifest.sqlite` forks whenever both boxes write
  `pose_status`, and the `sqlite3 ".backup"` snapshot is a one-way copy, not a
  sync. The answer is Postgres in a container on tofino, with both boxes pointed
  at it. That also removes the `--shard i/n` requirement: workers could claim
  rows atomically instead of pre-slicing the list. See
  [current_status.md](current_status.md) for why this is deferred and what makes
  it cheap to do later.

Sizing, for whenever the corpus grows: pose output as it stands is ~383 MB per
video against ~143 MB of source video — **pose is bigger than the raw it comes
from, and would be about two-thirds of total storage at full corpus scale.** Item
1 above is the cheapest fix available to that.

---

## Small things that will bite

- **`torch` is not installed in either pose env**, so
  `device.best_torch_device()`'s auto-detect never actually runs. Always pass
  `--accel-device cuda` or `mps` explicitly. The auto-detect path reads like it
  works; it doesn't.
- **`data/pose_overlays/` is 5.7 GB of sample renders**, produced by
  `scripts/sample_pose_overlays.py` for eyeballing. Not an output anything
  depends on — safe to delete and regenerate.
- **The detector is CPU-pinned on `mps` only**, because CoreML crashes at
  inference on YOLOX's dynamic NMS output shape. On CUDA it runs on the GPU.
  That asymmetry is intentional and already correct in the code.
