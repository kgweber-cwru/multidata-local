# Pose and 3D: where the work stands

The working note for body pose and the move to 3D: what's done, the open
problems, the plan, and what to do next. How each stage works is in
[pipeline.md](pipeline.md) (§9 pose, §9a views, §9b sync); the hands-on side
(room calibration) is in the
[roadmap](https://claude.ai/artifact/QTY1PoyKCDDb8qeX1Lxn1m) and the
[calibration session protocol](https://claude.ai/artifact/Py2pHvRrJXGRT12NLvfE8C).

**Goal:** 3D body pose for the small exam rooms from the two cameras already
in each room, for behavioural research and possibly learner assessment.

---

## Where it stands (2026-11-01)

| | |
|---|---|
| Scope | Small exam rooms only (two-digit rooms, built alike). Large rooms (`00 LA`, `00 LB`) can be ingested for transcription and 2D, but `pose`, `sync` and `views` skip them unless `--all-rooms` |
| 2D pose | HALPE_26 (rtmlib `BodyWithFeet`), re-run on every video from 2026-10-30. The earlier 133-keypoint WholeBody files were deleted |
| Sync | Audio offsets and frame times for every video; fails (never guesses) in room 11 and a few others |
| Camera movement | `views` stage + per-camera view library; recurring views provisionally named after the operators' presets |
| 3D | Not started: waiting on room calibration |
| Cost | ~35 GPU-minutes of pose per case; [cost_estimate.md](cost_estimate.md) |

---

## Open problems

### 1. Pose has no person identity

`keypoint` is `(M, T, V, C)`, and **M is a slot, not a person**: slot 0 in one
frame and the next can be different people. So nothing can yet be attributed
to a role ("did the learner lean in while the patient spoke?"), which is the
point of having pose next to transcripts.

The plan solves this in 3D rather than per camera: match people across the
two views by geometry, then track them over time in 3D, where people don't
jump between frames. Roles (learner, preceptor, patient) are a separate step
after that: position rules, matching people to the diarized speakers, or
labelling a few frames per case. `scripts/demo_clip.py` shows a crude
position-based role guess, labelled as a guess.

### 2. Mirrors: phantom people

Every exam room has a one-way window to the control room that acts as a
mirror when the control room is dark, and at least one camera in each room
sees it (seen in rooms 07, 11, 13, 14). **The pose model detects reflections
as people**, with whole, confident bodies; in one large-room case there were
more reflections per frame than real people. A 2D mask doesn't work (real
people stand in front of the window), and reflections took slots from real
people when there were only 4 (now 8).

The plan drops them in 3D, using the measured window planes from
calibration: a reflection seen by both cameras triangulates to a point behind
the glass; one seen by only one camera never matches across views. A second
pass removes 2D detections that match the mirror image of a real 3D person.
The same check drops people seen *through* the glass when the control room is
lit.

### 3. Cameras move

The cameras are PTZ and operators steer them: 5-20 moves an hour per camera
(survey, 2026-10-30). Both cameras hold a view 99% of the time, but a
calibration only holds for one view.

- Recurring views are the operators' **presets** (Drew, 2026-11-01): Baseline,
  Chair (End of Exam Bed), Bed (Full Length of Exam Bed), Sink, PC, and User
  (operator-defined). A preset moves **both** cameras of a room, so it shows
  up as a pair of views; the sink-side camera often stays wide while the
  bed-side one moves.
- Provisional labels (by eye, 2026-11-01): footage is 28% Baseline, 14% PC,
  10% Bed, 3% Chair, 1% Sink and 44% unmatched (operator-steered, or presets
  on cameras with too few cases to recur yet). Confirm them with
  `scripts/review_views.py` ([pipeline.md](pipeline.md) §9a).
- Operators don't see pan/tilt/zoom values, but the system might record them:
  that question is with the LearningSpace team. Until they answer, plan as if
  off-preset views must be located from the video.
- The exam table changes shape between cases (chair vs. flat bed), so it
  isn't a fixed reference point.

So 3D comes in tiers: on-preset stretches with a stored calibration (roughly
40-55% of the time with both cameras); off-preset stretches located from the
video against the pan/tilt sweep (or from PTZ logs, if they exist); the rest
no 3D. Some cases will get none, and that's accepted.

---

## The 3D plan

**Our own small 3D module, with Pose2Sim as a reference** (decided
2026-10-30). Pose2Sim assumes fixed cameras with one calibration per
project, frame number = time, short trials and ideally 4+ cameras; we have
two moving PTZ cameras, dropped frames, sub-frame start offsets, 20-70 minute
sessions and mirrors. Everything hard here (sync, view segmentation,
per-view calibration including zoom, mirror rejection) we build either way,
and what Pose2Sim adds on top (two-view triangulation, cross-view matching,
3D tracking) is small. So:

- The 3D module works on our timeline, our steady stretches and our window
  planes, using established libraries (OpenCV for triangulation, SciPy for
  assignment), and is tested on synthetic scenes first.
- Calibrations are stored in Pose2Sim's file format, so handing data to
  Pose2Sim or OpenSim later is cheap.
- Pose2Sim runs in its own env as a cross-check on its demo data and on one
  clean interval of ours. Traps if it's ever used: `fill_large_gaps_with`
  defaults to `'last_value'` (set `'nan'`), `frame_range = 'auto'` trims
  data (set `'all'`), its torch install example doesn't support the RTX 5080
  (needs CUDA 12.8+), and its face blurring re-encodes without audio and
  truncates the frame rate.

**Pruning:** prune by scope, never by convenience. 2D pose runs on every
frame of every video (no trimming of videos, no dropping off-preset time:
operators zoom in exactly when something clinical happens). Reflections and
low-confidence detections are flagged in derived layers, not deleted.

---

## Next steps, in order

1. **Detector stride:** read `docs/detect_every_test.md` (written by the
   test run on 2026-11-01); adopt `--detect-every 2` or `3` only if it passed
   the thresholds fixed before the run.
2. **Name views:** confirm the provisional preset labels with Drew.
3. **3D module on synthetic data:** calibration store, triangulation,
   two-view matching, 3D tracking, mirror-plane filter.
4. **Calibration session** in room 06 (protocol linked above), then lens
   calibration per zoom, camera position per preset, accuracy checks against
   the ground-truth shots (targets: lens error under 0.5 px, position about
   1 cm).
5. **First 3D:** room 06, on-preset stretches; then off-preset; then other
   rooms; then roles and head direction.

---

## Things that will bite

- **Room 11 can't be audio-synced** (camera 31's audio has gaps), so its
  cases get no 3D unless another sync method is found.
- **Some files drop minutes of frames** (261464/337 lost ~1170 s). Always
  pair frames by time, never by index.
- **Case 261573 is a 20-minute empty room** on both cameras; empty pose
  files are correct there.
- **The detector runs on CPU on a Mac** (CoreML can't run YOLOX's dynamic
  output); on CUDA both models use the GPU.
- **`data/pose_overlays/` and `data/demos/`** are renders for eyeballing;
  nothing depends on them.
