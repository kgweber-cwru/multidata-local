# Pose — restarted 2026-10-30 (was parked 2026-09-14)

**Read this before touching anything pose-related.** Pose is active again,
aimed at 3D: two calibrated cameras per small exam room, triangulated. This
note says what state the work is in and what to do next. Operational commands
stay in [running_job_notes.md](running_job_notes.md); architecture and
throughput in [../multidata_local_pipeline.md](../multidata_local_pipeline.md) §9.

---

## Where we are (2026-10-30)

| | |
|---|---|
| Model | rtmlib `BodyWithFeet`: YOLOX-m detector + RTMPose-m, **HALPE_26** (26 keypoints: COCO-17 body + head top, neck, mid-hip, 6 foot points). Five face points only -- a deliberate choice, partly for IRB |
| Scope | **3D pilot: small exam rooms only** (two-digit rooms, built alike). Large-room (`00 LA`, `00 LB`) data and case metadata deleted 2026-10-30; they can be ingested again for transcription/2D. `run_stage.py pose`/`sync` skip large rooms unless `--all-rooms` |
| Pose files | All WholeBody (133-keypoint) files deleted 2026-10-30. Every video is `pending` for a fresh HALPE_26 run |
| Output | `data/pose/<case_id>/<camera>.npz`: keypoints, scores and detector boxes (float16), up to 8 people per frame, plus OpenCV's time per frame (`frame_ms`) and the model/settings used. See `pose.py` |
| Throughput | 1.54x realtime on the first HALPE_26 clip (260656/23, RTX 5080), detection ~60% of the time -- in line with the old WholeBody baseline (1.67x). Every run's cost is now recorded in the manifest's `stage_runs` table |
| Runs on | `tofino` (RTX 5080, `cuda`), md-pose env. No torch needed |
| 3D plan | Our own 2D pose + audio sync feed our own small 3D module (calibration store, triangulation, two-view person matching, mirror filter), using well-established libraries (OpenCV, SciPy). Pose2Sim, in its own env, is a cross-check and a later route to OpenSim -- see "Why not Pose2Sim end to end" below |

### Why not Pose2Sim end to end (reviewed 2026-10-30)

Pose2Sim assumes fixed cameras with one calibration per project, frame number
= time, short trials and ideally 4+ cameras. We have two PTZ cameras that move
5-20 times an hour, dropped frames, sub-frame start offsets, 20-70 minute
sessions and mirrors. Everything hard here (sync, view segmentation,
per-segment calibration incl. zoom, mirror rejection) we'd build either way;
what Pose2Sim adds on top (triangulation, association, tracking) is small for
two views. Traps if it is used later: `fill_large_gaps_with` defaults to
`'last_value'` (set `'nan'`), `frame_range = 'auto'` trims (set `'all'`),
`undistort_points` listed as not implemented, its face blurring re-encodes
without audio and truncates the frame rate.

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

### Mirrors: phantom people (found 2026-10-29)

Every exam room we've looked at has a wall mirror (the one-way window to the
control room, which reflects when the control room is dark), and at least one
camera in each room sees it: rooms 7 (cams 24 and 33), 11 (16), 13 (290) and
14 (13). The numbers below come from case 255387 (large room 00 LA, since
dropped), but the HALPE_26 test clip in room 09 shows the same thing. These are from spot-check stills, not a full survey. **The pose model detects reflections as people.** Case 255387, camera
327: across the whole video there are on average 1.2 reflected "people" per
frame, and 1.9 per frame during the 2:25–3:10 demo clip. That's more
reflections than real people. This is why that camera reports 4 people in
almost half its frames, though only two people are in the room.

Why it's hairy rather than a quick mask:

- **Real people overlap the mirror.** A learner standing at the chair has their
  head in the mirror's area of the frame. Masking every keypoint inside the
  mirror cuts off real heads, and masking by bounding box drops real people.
- **Reflections look like whole, confident bodies.** Detection scores alone
  won't filter them out.
- **They hit everything downstream.** They take up slots (only 4 per frame, so
  a reflection can push out a real person), they create false learner/patient
  candidates for identity, and in 3D work a reflection is effectively a third
  "camera" whose geometry is mirrored.

What worked for the demo (`255387_two_view_2m25-3m10.mp4`): draw a mirror
outline by hand for each camera, and call a detection a reflection when ≥ 80%
of its confident body keypoints fall inside that outline. That's enough for a
45-second clip on a fixed camera. It isn't a pipeline answer: the outline is
per camera, has to be redrawn whenever the camera moves, and hasn't been
checked in other rooms. Better routes, neither tried yet: tag mirror outlines
per camera in `db_seed/camera_rooms.csv`, or flag a detection that is the
mirror image of a real person in the same frame.

### Syncing cameras (2026-10-29, branch `calibration-sync`)

Triangulation needs the two cameras' frames paired by time. Two facts about
the raw files decide how:

- **A case's two files start up to ~1 s apart, by a fixed amount, with no
  drift.** Both carry the same audio feed, so audio cross-correlation gives
  the offset to the sample (always a multiple of 32 ms). Examples: 255387
  is 0.832 s, 260723 is 0.192 s.
- **Files drop video frames.** Most lose a frame or two, but some lose a lot:
  261464/337 lost ~1170 s, 252850/4 ~310 s, room 11's camera 16 (261456,
  261505) ~230–270 s. So frame number is not time.

The `sync` stage (`run_stage.py sync`, md-speech; `multidata.sync`) writes
`videos.sync_offset_s` (seconds to add to this file's time to get the
reference camera's time; reference = `cases.audio_camera`, offset 0),
`videos.sync_agreement`, and per-frame timestamps to
`data/sync/<case_id>/<camera>.npz`. Pose frame `i` of camera `c` happened at
`frame_time_s[i] + sync_offset_s[c]` (`sync.reference_times()`); pair the two
cameras with `sync.nearest_frames()`. No pose rerun was needed.

Backfilled 2026-10-29: 65 rows done, 4 failed (the offset is left NULL, never
guessed):

| Case/camera | Why |
|---|---|
| 261456/31, 261505/31 | Room 11: camera 16's audio has gaps too, so windows disagree |
| 261041/41 | Only 58% of windows agree. Not looked at yet |
| 260656/26 | 2-minute clip, too few windows (pose also failed on it) |

Two wrinkles:

- **OpenCV sometimes skips frames the file has** (5 files, all with big
  drops), so pose has fewer frames than the timestamp list.
  `scripts/fix_frame_times.py` (md-pose) re-reads those files with OpenCV and
  keeps only the timestamps of the frames pose saw. Re-running `sync` on a row
  undoes that, so run the fix script after. `sync.reference_times(...,
  n_frames=T)` refuses a mismatch rather than pairing wrongly.
- **Most files repeat one timestamp about 0.5 s in** (two frames at
  0.467 s, none at 0.5 s). That's in the files, and costs one frame at most
  33 ms.

### Cameras move (survey 2026-10-30)

The cameras are PTZ and an operator steers them during sessions. A survey of
all small-room footage (18 h, 29 two-camera cases; keyframes only, ORB feature
matching to detect view changes) found:

- **5-20 moves an hour** per camera, following the action and zooming in on
  the bed. Room 07 moves least (~2/hour).
- Both cameras are holding a view (30 s or more) **99%** of the time, but both
  are in a recurring position only **~39%** of the time.
- Recurring positions are the presets: home/wide, exam table, desk,
  sink/counter. In room 06 (7 cases), 3-4 of them cover ~53% of each camera's
  time.
- 56% of moves happen on both cameras within 3 s: presets look paired.
- The exam table changes shape between cases (chair vs. flat), so it is not a
  fixed reference.

So 3D comes in tiers: on-preset stretches with a stored calibration;
off-preset stretches located from the video (or from logged PTZ values, if
the control system has them); the rest no 3D. Correction to an earlier note:
camera 327 (00 LA) did move early in 255387, not only at the end.

**The `views` stage** (`run_stage.py views`, md-pose, `multidata.views`) now
does this for every video: steady stretches in `data/views/<case>/<camera>.json`
(file-clock and reference-clock times, view id, match quality, move from the
previous stretch), a per-camera view library with stable ids
(`data/views/library/<camera>/v<n>.npz/.png`), and in the manifest
`camera_views` (one row per view, with a label: provisional until someone
names it, e.g. from Drew's preset list -- `manifest.set_view_label()`) and
`video_views` (seconds per video per view). Which views are presets is a query
(`manifest.view_summary()`: seen in 2+ cases or held 3+ times), so it updates
as cases arrive. Run it as one worker, oldest videos first (the runner does).
The original survey outputs are kept in `data/views/survey_2026-10-30/`.

**Presets (Drew, 2026-11-01):** Baseline, Chair (End of Exam Bed), Bed (Full
Length of Exam Bed), Sink, PC, and User (operator-defined). Two or three
operators run the cameras; the system does not record pan/tilt/zoom values
(Drew), and the LearningSpace team has the remaining technical questions. A
preset moves both cameras of a room together, so a preset is a *pair* of
views; the sink-side camera often stays wide while the bed-side one moves.
All 65 recurring views got provisional labels by eye on 2026-11-01
(`scripts/label_views_2026-11-01.py`, confidence in each view's notes):
footage is 28% Baseline, 14% PC, 10% Bed, 3% Chair, 1% Sink, 44% unmatched
(operator-steered, or presets on cameras with too few cases to recur).
Contact sheets: `data/views/sheets/`. Confirm with
`manifest.set_view_label(camera, view_id, label, "confirmed")`.

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
