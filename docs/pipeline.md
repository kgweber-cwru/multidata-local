# Pipeline reference

How each stage works, what it reads and writes, and why it is built the way
it is. For running jobs day to day see [operations.md](operations.md); for
where the project stands, [status.md](status.md). Section numbers are stable:
code docstrings cite them ("doc §10").

Everything runs on **tofino** (Linux, RTX 5080), the only machine that holds
`data/` and writes `manifest.sqlite`.

---

## 0. Design principles

1. **Raw is immutable.** Source videos land once and are never edited; every
   other file is derived and can be regenerated.
2. **Stages are separate and resumable.** Each stage reads files, writes
   files, and records its status per video in the manifest, so one stage can be
   re-run for one video without redoing the rest.
3. **Separate environments for incompatible stacks.** Speech (torch, whisper,
   pyannote) and pose (rtmlib, onnxruntime) never share an env (§3).
4. **Everything is provenanced.** Model, version, settings and input hash go
   with each output; every stage run's cost is recorded (`stage_runs`, §10).
5. **One writer.** Only tofino writes the manifest (§10).

---

## 1. Machines

| Machine | Role |
|---|---|
| **tofino** (Linux, RTX 5080, 48 cores), `~/projects/multidata-local` | Everything: all stages, ingest, benchmarks, the only manifest writer. Nightly backup to its `/wddata` array ([operations.md](operations.md)). |
| Kate's MacBook Air | Development; code moves through GitHub. Its `data/` is an old copy. |

Setting up a new Linux machine: install miniforge/miniconda, clone the repo,
create the envs (§3), log in to Hugging Face (§3). ffmpeg comes with the envs.

---

## 2. Repository layout and naming

```
src/multidata/   the package: stages + helpers (module map in its __init__.py)
scripts/         command-line tools; run_stage.py runs any stage
notebooks/       ingest_walkthrough.ipynb (committed how-to) + personal scratch
benchmarks/      ASR benchmarking wing (§8)
annotation/      hand-annotation kits and who has which pass
elan/            the versioned ELAN template for gold annotation
db_seed/         git-tracked copy of the hand-verified camera->room table
env/             conda env files (§3)
docs/            this and the other guides
tests/           pytest (tests/README.md)
data/            GITIGNORED: raw video and every derived file (below)
logs/            GITIGNORED: run_stage.log, PID files, job scripts
manifest.sqlite  GITIGNORED: the registry (§10)
```

`data/` holds one folder per kind of output, each with a folder per case:
`raw/` (flat, see below), `audio/`, `transcripts/`, `diarization/`, `elan/`,
`prosody/`, `sync/`, `pose/`, `views/`, plus `gold/` for gold annotation and
`demos/` for demo clips. Files are named `<case_id>/<camera>.<ext>`.

### File names and case matching

`data/raw/` is flat: one file per camera per encounter, named by the source
system as **`<date:YYYYMMDD>-<time:HHMMSS>-<recording_id>-<camera>.mp4`**,
e.g. `20260224-132704-v308933-41.mp4` = 2026-02-24, 13:27:04, source video
`v308933`, camera 41.

**The file name has no case id.** A file is matched to its case
(`casematch.resolve`) by:

1. **recording time**, to the minute (the cases table only has minutes), and
2. **room**, through the camera: the `cameras` table maps each camera to its
   room. That mapping is hand-verified (nothing in the source data says it);
   change it with `manifest.set_camera_room(camera, room)`, then
   `manifest.export_camera_rooms("db_seed/camera_rooms.csv")` and commit.

Results are cached in `video_case_matches`; files that didn't match show up in
`manifest.unresolved_matches()`. Every output is keyed by `(case_id, camera)`.

---

## 3. Environments

The env files in `env/` are the source of truth; create each with
`conda env create -f env/<file>.yml`, then `pip install -e .` inside it (the
package declares no dependencies of its own, on purpose: they live in the env
files).

| Env | File | For |
|---|---|---|
| `md-utility` | `utility.yml` | notebooks, the Excel case import, ingest |
| `md-speech` | `speech.yml` | audio, asr, diarize, elan, prosody, sync, benchmarks |
| `md-pose` | `pose-nvidia.yml` (Linux/CUDA) or `pose.yml` (Mac) | pose, views, demo clips |

- **md-pose on Linux needs one extra step** after creating it:
  `pip install --force-reinstall --no-deps "onnxruntime-gpu[cuda,cudnn]"`.
  rtmlib pulls in the CPU-only `onnxruntime`, which can silently win; the top
  of `env/pose-nvidia.yml` explains and shows how to check.
- **pyannote is gated:** `huggingface-cli login` once, accept the terms for
  `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`, and keep
  the token in `.env` (gitignored).
- Scripts find `ffmpeg`/`ffprobe` in the env they run from even when it isn't
  activated (`multidata.tools.which`), and `run_stage.py` stops with one clear
  message if a stage is run in the wrong env.

---

## 4. Ingest

1. Cases come first, from the source system's Excel export:
   `python -m multidata.manifest export.xlsx` (md-utility). It only adds cases
   it doesn't have.
2. Copy camera files into `data/raw/`, names unchanged.
3. `python scripts/ingest_raw.py` lists what it would register (name check,
   case match, ffprobe facts, SHA-256 duplicate check); `--write` registers
   them with every stage `pending`.

`notebooks/ingest_walkthrough.ipynb` does the same one step at a time, then
runs the stages; it's the place to learn the pipeline. A scripted download
from the recording system is not built (and shouldn't be until manual ingest
has been routine for a while).

---

## 5. Audio

Whisper and pyannote want 16 kHz mono WAV:
`ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -sample_fmt s16 out.wav` (`audio.py`).

**A case's two camera files carry the same audio feed** (cross-correlation
across rooms, 2026-07-29: same samples, started up to ~1 s apart). So the
audio stages (audio, asr, diarize, elan, prosody) run **once per case**, on
the case's **audio camera** (`manifest.ensure_audio_camera`: lowest camera
id, stored in `cases.audio_camera`, sticky). The other camera's row is marked
`skipped` for those stages. The same shared feed is what makes camera sync
possible (§9b).

Optional `--loudnorm` normalizes loudness. Don't denoise before benchmarking:
you'd be measuring the denoiser.

---

## 6. Speech: transcription and diarization

Default: **`faster_whisper`, model `medium`, language `en`** (settled
2026-07-29: with a separate diarization merge it ran ~11% faster than
whisperx's bundled pipeline at the same model size). Other engines via
`--engine`: `whisperx` (wav2vec2 forced alignment, arguably better word
timing) and `whisperx_disfluent` (tuned to keep disfluencies; experimental).

Every engine's output is **word-level and speaker-labelled**
(`segments[].words[]` with `start, end, word, score, speaker`). Engines that
don't diarize get pyannote's result merged in (`asr._diarize_and_merge`),
reusing `data/diarization/<case>/<camera>.rttm` if it exists. The `asr` stage
therefore also marks `diarize_status` done; the separate `diarize` stage is
for refreshing diarization on its own.

Output: `data/transcripts/<case>/<camera>_<engine>_<model>.json` (the source
of truth for ELAN and benchmarking) and a `.txt` beside it for reading.

### 6a. Decoding hygiene

Two failure modes dominate on clinical audio; a bigger model fixes neither.

- **Hallucinated phrases over silence** ("Thank you"). Biggest lever: VAD
  gating (`vad_filter=True`; whisperx does VAD by default). Also
  `condition_on_previous_text=False` and the usual thresholds
  (`no_speech_threshold≈0.6`, `compression_ratio_threshold≈2.4`,
  `log_prob_threshold≈-1.0`).
- **Domain substitutions** ("nurse" -> "nerds"): an `initial_prompt` with
  domain terms (`asr.DEFAULT_PROMPT`) biases the decoder softly. Treat the
  prompt as an experimental variable in benchmarks, not a constant.

`medium` is the batch default; use `--model large-v3` for accuracy-sensitive
runs and as the benchmark accuracy anchor.

### 6b. Prosody (`run_stage.py prosody`, md-speech, CPU)

Per case, on the audio camera, after `asr` (it needs the diarization).
`prosody.py`:

- **Frame features** -> `data/prosody/<case>/<camera>.npz`, every 10 ms:
  `f0_hz` (pitch; NaN unvoiced), `intensity_db` (uncalibrated: compare within
  a recording), `hnr_db` (voice quality; NaN in silence), `f1_hz`..`f4_hz`
  (formants). Praat defaults (pitch 75-600 Hz); versions stored in the file.
- **Per-speaker summary** -> manifest `speaker_prosody` and
  `data/prosody/<case>/<camera>_speakers.csv`: turns, speaking, overlap and
  voiced time, median pitch, 10th/90th percentile, range and spread in
  semitones, loudness and HNR while voiced, `pitch_bucket`. Frames where
  diarized speakers overlap are left out of every speaker's figures.

Speaker labels (`SPEAKER_00`, ...) are per case, not people across cases.
`pitch_bucket` is a rough vocal-register label for picking annotation samples,
never a statement about a person. `manifest.export_speaker_prosody(path)`
writes all cases' speaker rows to one CSV.

---

## 7. ELAN files

`elan.py` builds one `.eaf` per case from the transcript: word-level, one
tier per speaker, linked to the audio camera's video (so the picture matches
the audio). Output: `data/elan/<case>/<camera>.eaf`.

**Gold annotation does not start from this draft** (settled 2026-08-26):
correcting a machine draft biases the reference toward that machine. Gold is
made blind, from scratch, in ELAN from `elan/template.etf`; the draft is used
only in the adjudication pass. See
[transcription_standards.md](transcription_standards.md) §9 and
[annotator_guide.md](annotator_guide.md). Gold files live under
`data/gold/<case>/` and are never overwritten by a re-run.

---

## 8. ASR benchmarking

Measures how well ASR and diarization work on this audio, and compares
engines. The design (local + cloud providers, glossary, scoring profiles) is
[asr_provider_spec.md](asr_provider_spec.md); what's built is
[asr_provider_implementation_plan.md](asr_provider_implementation_plan.md).
`benchmarks/run_benchmark.py` runs and scores one configuration;
`scripts/bench_status.py` shows what's been scored against what. Expect
higher error rates than published numbers (medical vocabulary, overlapping
speech, multi-party rooms): that gap is a finding; report it.

### 8a. Gold references and frozen audio

Gold is the expensive, essential part:
`benchmarks/references/<case>.gold.txt` (and `.gold.rttm`), exported from an
adjudicated `.eaf` by `scripts/eaf_to_gold.py`, never typed by hand. The
audio a benchmark scores must never change; its hash is recorded with every
run.

### 8b. Metrics

| Question | Metric | Tool |
|---|---|---|
| Transcription accuracy | WER, CER | `jiwer` |
| Speaker attribution | DER, JER | `pyannote.metrics` (not wired yet, plan Phase 6.2) |
| Word timing | boundary error vs gold | not built |

`normalize.py` (the L0 verbatim / L1 filler-neutral scoring profiles) and
`providers.py` (the capability registry loader) are built and tested, but
`run_benchmark.py` still uses its own simple normalization: wiring them in is
plan Phase 6.

### 8c. Sweep design

Vary **one axis at a time**, everything else pinned: model size, engine,
VAD, speaker-count hints, prompt, audio preparation (raw vs. normalized).
Engine responses are cached (`multidata.bench_runs`), so a repeated
identical run costs nothing.

### 8d. Provenance

Every run appends a row to `benchmarks/results/runs.csv` (append-only, in
git): engine, model, settings, audio hash, gold hash, commit, wall time,
machine; the full config and normalized output go in
`benchmarks/runs/<run_id>/` (gitignored, since it holds transcript text). A
result that can't be traced to an exact configuration is noise.

---

## 9. Pose (`run_stage.py pose`, md-pose, GPU)

`pose.py` runs rtmlib's `BodyWithFeet` models: a YOLOX-m person detector and
RTMPose-m trained on **HALPE_26** (the 17 COCO body joints in COCO order, then
head top, neck, mid-hip and six foot points; `pose.KEYPOINTS`). Only five
points are on the face, deliberately (IRB).

Output per camera, `data/pose/<case>/<camera>.npz`: keypoints `(M, T, 26, 2)`,
scores, detector boxes (float16; at most 0.5 px error inside a 1920-wide
frame), OpenCV's time for each frame (`frame_ms`), and the model and settings.
`M` = 8 slots per frame. **A slot is not a person**: slot 0 in one frame and
the next can be different people (no tracker). Identity is the main open
problem; see [pose_3d.md](pose_3d.md).

Frames with nobody detected skip the pose network. A video with nobody in it
is a valid, empty result.

**Speed** (RTX 5080, 2026-10-31): 1.1x realtime per worker with two workers
sharing the GPU, 2.2x combined; detection is ~80% of the time. Run two
workers with `--shard 0/2` and `--shard 1/2` (the manifest has no row
locking, so unsharded workers would duplicate each other). `--detect-every N`
runs detection every Nth frame; whether that changes results is tested by
`scripts/test_detect_every.py` (report: `docs/detect_every_test.md`).

GPU notes: CUDA needs `onnxruntime.preload_dlls()` before sessions are made
(done in `pose.load_models`), or onnxruntime silently falls back to CPU.
No torch in md-pose: device is `--accel-device cuda`, or detected from
onnxruntime (`pose.default_device`).

---

## 9a. Camera views (`run_stage.py views`, md-pose) and naming presets

The cameras are PTZ: operators move them 5-20 times an hour, mostly by
recalling presets (Drew, 2026-11-01: Baseline, Chair (End of Exam Bed), Bed
(Full Length of Exam Bed), Sink, PC, User). A 3D calibration only holds while
a camera holds still, so `views.py` splits each video into **steady
stretches** and gives each one a **view id** (`v1`, `v2`, ... per camera) by
matching it against that camera's **view library** of sample frames. Ids
never change once given. Run it after `sync` (so stretches get
reference-clock times), as one worker; the runner goes oldest video first.

| Output | Where |
|---|---|
| Stretches per video (times, view id, match quality, move from previous) | `data/views/<case>/<camera>.json` |
| View library: 5 sample frames per view (`.npz`), same as a strip (`.png`) | `data/views/library/<camera>/v<n>.*` |
| One row per view: `label`, `label_source` (`provisional`/`confirmed`), `notes` | manifest `camera_views` |
| Seconds per video per view | manifest `video_views` |

A view is **recurring** when it shows up in 2+ cases or 3+ separate times
(`manifest.view_summary()`); recurring views are the presets. A preset moves
both cameras of a room, so it shows up as a recurring *pair* of views
(`views.pair_views()`).

**Naming views** (`scripts/review_views.py`):

1. `python scripts/review_views.py sheets` (md-pose) redraws
   `data/views/sheets/`: `recurring_<n>.png` (every recurring view, tagged
   `R<room> c<camera> v<n> <share>% <label>`, `*` = confirmed) and
   `pairs_room<NN>.png` (what both cameras hold at the same time).
2. `python scripts/review_views.py export` writes
   `data/views/review/labels.csv`: one row per recurring or labelled view,
   with the current label, its notes (who guessed it and how sure), and the
   sample strip to look at.
3. Fill in `confirmed_label` for the rows you've checked: a preset name
   exactly as in `manifest.PRESETS`, or `none` for "not a preset"; blank
   rows are skipped. `confirm_notes` is optional.
4. `python scripts/review_views.py import` shows what would change;
   `import --write` saves it and moves the sheet to `labels_imported.csv`.
   Then run `sheets` again to see the result.

After new cases go through `views`, repeat from step 1: new views appear with
no label. The first 65 recurring views were labelled provisionally by eye on
2026-11-01 (`scripts/label_views_2026-11-01.py`, confidence in each note).

---

## 9b. Camera sync (`run_stage.py sync`, md-speech, CPU)

To pair the two cameras' frames in time (for 3D, and for demo clips):

- **A case's two files start up to ~1 s apart, by a fixed amount, with no
  drift.** Both carry the same audio (§5), so cross-correlating it (GCC-PHAT
  over 10 s windows every 30 s) gives the offset. Stored as
  `videos.sync_offset_s`: seconds to add to this file's time to get the
  reference camera's time (the audio camera, offset 0), with
  `videos.sync_agreement` (share of windows agreeing). When windows disagree
  the offset stays NULL and the row is `failed`; it is never guessed.
- **Files drop frames**, sometimes minutes of them, so frame number is not
  time. The stage stores every frame's presentation time in
  `data/sync/<case>/<camera>.npz`.

A pose frame `i` happened at `frame_ms[i] / 1000 + sync_offset_s` on the
reference clock (pose stores the time of every frame it read, so this works
even where OpenCV skipped frames; `sync.pose_reference_times()`).
`sync.nearest_frames()` pairs two cameras' frames by time.

Known failures: room 11 (camera 31's audio has gaps), 261041/41, and very
short clips.

---

## 10. The manifest

`manifest.sqlite` (SQLite, gitignored, written only on tofino). All database
code is in `manifest.py`, which imports only the standard library so it works
in every env. Additive column migrations run automatically on connect.

| Table | One row per | Notes |
|---|---|---|
| `cases` | encounter, from the source system's export | `case_id` is the export's sheet title. Holds real names (learner, SP, preceptor): never print them. `audio_camera`, `cloud_release`, `consent_ref` live here |
| `videos` | camera file | what every stage reads and writes: file facts, `<stage>_status` per stage, `sync_offset_s` |
| `cameras` | camera | hand-verified camera -> room (§2); seeded from `db_seed/camera_rooms.csv` |
| `video_case_matches` | raw file name | cache of case matching: `matched`, `ambiguous`, `no_match`, `no_room` |
| `gold` | gold reference | who annotated, when frozen, which span |
| `stage_runs` | stage run on one video | wall time, media time, realtime factor, host, GPU, commit, flags, stage details, error; feeds [cost_estimate.md](cost_estimate.md) |
| `camera_views`, `video_views` | view; video x view | §9a |
| `speaker_prosody` | diarized speaker in a case | §6b |

**Statuses**: `pending`, `done`, `failed`, `skipped`. Each stage processes
only `pending` rows and updates status, so batch runs are restartable: kill
one, re-run it, it picks up where it stopped. `skipped` means the stage
doesn't apply to that row (the non-audio camera for audio stages), not a
failure. To redo a row, set its status back to `pending`.

`filepath` and `audio_path` are relative to the repo root. If a copy of the
manifest is needed elsewhere, take `sqlite3 manifest.sqlite ".backup <path>"`
(never `cp` a live database) and treat the copy as read-only.

---

## 11. Scale, governance, reproducibility

- **Cost:** about 39 machine-minutes and 0.56 GB per case, 90% of the compute
  being 2D pose; the full small-room catalogue doesn't fit on tofino's main
  drive. Details and scenarios: [cost_estimate.md](cost_estimate.md).
- **What's irreplaceable:** the manifest and the gold/annotation `.eaf`
  files. Raw video can be re-exported from the recording system; everything
  else regenerates. Backups: [operations.md](operations.md).
- **Consent and cloud release are pipeline fields:** `cases.consent_ref` and
  `cases.cloud_release`. No third-party ASR call may happen for a case without
  `cloud_release`; that check is **not enforced in code yet** (see
  [status.md](status.md)).
- **Pin and record versions.** Whisper, pyannote and rtmlib change behaviour
  across releases; outputs record model and settings, and a dataset release
  should use one pipeline version throughout (re-run rather than mix).
- **Quality is sampled, not exhaustive:** check a stratified subset against
  human annotation and report error rates, rather than treating every output
  as gold.

---

## Appendix: command reference

```bash
# register new raw files (md-utility or md-speech)
python scripts/ingest_raw.py            # dry run
python scripts/ingest_raw.py --write

# stages, in order (each processes only its pending rows; --only <case> for one case)
conda activate md-speech
python scripts/run_stage.py audio
python scripts/run_stage.py asr         # --engine whisperx --model large-v3 to vary
python scripts/run_stage.py elan
python scripts/run_stage.py prosody
python scripts/run_stage.py sync
conda activate md-pose
python scripts/run_stage.py views       # one worker
python scripts/run_stage.py pose --accel-device cuda --shard 0/2   # and 1/2 alongside

# name camera views (md-pose for sheets)
python scripts/review_views.py sheets | export | import [--write]

# demo clip of a case (md-pose)
python scripts/demo_clip.py 261498

# benchmarking (md-speech)
python scripts/eaf_to_gold.py --eaf <path.eaf> --case <case_id>
python benchmarks/run_benchmark.py --case <case_id> --audio data/audio/<case_id>/<camera>.wav \
    --engine whisperx_disfluent --model medium
python scripts/bench_status.py
```
