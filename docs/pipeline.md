# Pipeline reference

How each stage works, what it reads and writes, and why it is built the way
it is. The exact layout of every output file is in
[data_formats.md](data_formats.md). For running jobs day to day see [operations.md](operations.md); for
where the project stands, [status.md](status.md). Section numbers are stable:
code docstrings cite them ("doc §10").

Everything runs on one **processing host**, the only machine that holds
`data/` and writes `manifest.sqlite`. During the pilot that is `tofino`, a
Linux workstation with an RTX 5080; after the pilot the work moves to a
high-performance cluster, so nothing here should depend on tofino itself.

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
5. **One writer.** Only the processing host writes the manifest (§10).

---

## 1. Machines

| Machine | Role |
|---|---|
| **Processing host**, during the pilot `tofino` (Linux, RTX 5080, 48 cores), `~/projects/multidata-local` | Everything: all stages, ingest, benchmarks, the only manifest writer. On tofino, nightly backup to its `/wddata` array ([operations.md](operations.md)). |
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

`data/` holds one folder per kind of output, each with a folder per recording:
`raw/` (flat, see below), `audio/`, `transcripts/`, `diarization/`, `elan/`,
`prosody/`, `sync/`, `pose/`, `views/`, plus `gold/` for gold annotation and
`demos/` for demo clips. Files are named `<recording_id>/<camera>.<ext>`.

### File names and recording matching

`data/raw/` is flat: one file per camera per encounter, named by the source
system as **`<date:YYYYMMDD>-<time:HHMMSS>-<file_number>-<camera>.mp4`**,
e.g. `20260224-132704-v308933-41.mp4` = 2026-02-24, 13:27:04, source video
`v308933`, camera 41.

**The file name has no recording id.** A file is matched to its recording
(`filematch.resolve`) by:

1. **recording time**, to the minute (the recordings table only has minutes), and
2. **room**, through the camera: the `cameras` table maps each camera to its
   room. That mapping is hand-verified (nothing in the source data says it);
   change it with `manifest.set_camera_room(camera, room)`, then
   `manifest.export_camera_rooms("db_seed/camera_rooms.csv")` and commit.

Results are cached in `file_matches`; files that didn't match show up in
`manifest.unresolved_matches()`. Every output is keyed by `(recording_id, camera)`.

LearningSpace knows each recording's files by exact name, so
`scripts/ls_backfill.py` checks every match after the fact and fills in the
file links ([learningspace.md](learningspace.md)). The time-and-room match
above is what ingest still uses; replacing it with the API is the next step.

---

## 3. Environments

The env files in `env/` are the source of truth; create each with
`conda env create -f env/<file>.yml`, then `pip install -e .` inside it (the
package declares no dependencies of its own, on purpose: they live in the env
files).

| Env | File | For |
|---|---|---|
| `md-utility` | `utility.yml` | notebooks, the Excel recording import, ingest |
| `md-speech` | `speech.yml` | audio, asr, diarize, elan, prosody, sync, benchmarks |
| `md-pose` | `pose-nvidia.yml` (Linux/CUDA) or `pose.yml` (Mac) | pose, views, demo clips |

- **md-pose on Linux needs one extra step** after creating it:
  `pip install --force-reinstall --no-deps "onnxruntime-gpu[cuda,cudnn]"`.
  rtmlib pulls in the CPU-only `onnxruntime`, which can silently win; the top
  of `env/pose-nvidia.yml` explains and shows how to check.
- **pyannote is gated:** `huggingface-cli login` once, accept the terms for
  `pyannote/speaker-diarization-community-1`, and keep the token in `.env`
  (gitignored). `multidata.hf_auth` also switches off pyannote 4's usage
  telemetry (file durations and speaker counts sent to pyannote.ai);
  `set_telemetry_metrics(False, save_choice_as_default=True)` has been run in
  md-speech as well, and should be on any new machine.
- Scripts find `ffmpeg`/`ffprobe` in the env they run from even when it isn't
  activated (`multidata.tools.which`), and `run_stage.py` stops with one clear
  message if a stage is run in the wrong env.

---

## 4. Ingest

1. Recordings come first, from the source system's Excel export:
   `python -m multidata.manifest export.xlsx` (md-utility). It only adds recordings
   it doesn't have.
2. Copy camera files into `data/raw/`, names unchanged.
3. `python scripts/ingest_raw.py` lists what it would register (name check,
   recording match, ffprobe facts, SHA-256 duplicate check); `--write` registers
   them with every stage `pending`.
4. `python scripts/ls_backfill.py`, then again with `--apply`: links each
   recording to LearningSpace by filename and fills in its GUIDs, exact start
   time, file links, preceptor and grade ([learningspace.md](learningspace.md)).

`notebooks/ingest_walkthrough.ipynb` does the same one step at a time, then
runs the stages; it's the place to learn the pipeline. A scripted download
from the recording system is not built (and shouldn't be until manual ingest
has been routine for a while).

---

## 5. Audio

Whisper and pyannote want 16 kHz mono WAV:
`ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -sample_fmt s16 out.wav` (`audio.py`).

**A recording's two camera files carry the same audio feed** (cross-correlation
across rooms, 2026-07-29: same samples, started up to ~1 s apart). So the
audio stages (audio, asr, diarize, elan, prosody) run **once per recording**, on
the recording's **audio camera** (`manifest.ensure_audio_camera`: lowest camera
id, stored in `recordings.audio_camera`, sticky). The other camera's row is marked
`skipped` for those stages. The same shared feed is what makes camera sync
possible (§9b).

Optional `--loudnorm` normalizes loudness. Don't denoise before benchmarking:
you'd be measuring the denoiser.

---

## 6. Speech: transcription and diarization

Default: **`whisperx_disfluent`, model `large-v3`, language `en`** (since
2026-11-02). The standards keep disfluencies (transcription standards §1), and
this engine is decode-tuned to keep them; it is also the fixed seed engine for
draft-seeded gold (§8a), so a recording's pipeline transcript *is* its seed
transcript. Other engines via `--engine`, for comparison: `faster_whisper`
(the default until 2026-11-02, tuned to drop disfluencies) and `whisperx`.

**Diarization** is `pyannote/speaker-diarization-community-1` (since
2026-11-02; was `3.1`). Run `diarize` before `asr`: every engine, whisperx
included, takes its speaker labels from the diarize stage's
`data/diarization/<recording>/<camera>.rttm` (`asr._diarize_and_merge`), so the
transcript, the RTTM and prosody all use the same `SPEAKER_nn` labels. If the
RTTM is missing, `asr` makes it. On four human-annotated recordings community-1
cut speaker confusion (right speech, wrong speaker) from 2-8% of speech to
2-4%, with missed speech unchanged (~8-20%, mostly backchannels and overlap).
Neither model separates two similar voices reliably: on 261411 both put the
learner and the patient on one speaker.

Every engine's output is **word-level and speaker-labelled**
(`segments[].words[]` with `start, end, word, score, speaker`).

The `asr` stage never overwrites a transcript a seeded annotation kit was
built from (`seeding.kit_seed_transcripts`): the kit records that file's
hash. The earlier `faster_whisper_medium` transcripts stay beside the new
ones; `data/diarization_pyannote31/` keeps the 3.1 RTTMs.

Output: `data/transcripts/<recording>/<camera>_<engine>_<model>.json` (the source
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

`whisperx_disfluent` deliberately leaves `condition_on_previous_text` on
(it carries the disfluent register across segments), which is also the main
cause of repetition loops on bad audio: check its benchmark scores for that.

### 6b. Prosody (`run_stage.py prosody`, md-speech, CPU)

Per recording, on the audio camera, after `asr` (it needs the diarization).
`prosody.py`:

- **Frame features** -> `data/prosody/<recording>/<camera>.npz`, every 10 ms:
  `f0_hz` (pitch; NaN unvoiced), `intensity_db` (uncalibrated: compare within
  a recording), `hnr_db` (voice quality; NaN in silence), `f1_hz`..`f4_hz`
  (formants). Praat defaults (pitch 75-600 Hz); versions stored in the file.
- **Praat files** beside it: `<camera>.PitchTier`, `.IntensityTier` and a
  `.TextGrid` of speaker turns, to open with the WAV in Praat.
- **Per-speaker summary** -> manifest `speaker_prosody` and
  `data/prosody/<recording>/<camera>_speakers.csv`: turns, speaking, overlap and
  voiced time, median pitch, 10th/90th percentile, range and spread in
  semitones, loudness and HNR while voiced, `pitch_bucket`. Frames where
  diarized speakers overlap are left out of every speaker's figures.

Speaker labels (`SPEAKER_00`, ...) are per recording, not people across recordings.
`pitch_bucket` is a rough vocal-register label for picking annotation samples,
never a statement about a person. `manifest.export_speaker_prosody(path)`
writes all recordings' speaker rows to one CSV.

---

## 7. ELAN files

`elan.py` builds one `.eaf` per recording from the transcript: word-level, one
tier per speaker, linked to the audio camera's video (so the picture matches
the audio). Output: `data/elan/<recording>/<camera>.eaf`. If prosody has run, a
`PITCH_HZ` tier shows the median pitch of each stretch of voicing; it comes
from the audio alone, so re-running ASR (then `elan`) leaves it unchanged.
Run `prosody` before `elan`.

**Gold annotation never starts from this draft.** Gold pass 1 is either
blind (empty template) or, when annotators are scarce, seeded from a
*separate*, fixed machine transcript (`whisperx_disfluent` / `large-v3`)
whose bias is recorded and kept out of benchmarking
([transcription_standards.md](transcription_standards.md) §9). This draft is
for browsing and for the adjudication pass. Procedure:
[annotation/README.md](../annotation/README.md). Gold files live under
`data/gold/<recording>/` and are never overwritten by a re-run.

---

## 8. ASR benchmarking

Measures how well ASR and diarization work on this audio, and compares
engines. The design (local + cloud providers, glossary, scoring profiles) is
[asr_provider_spec.md](asr_provider_spec.md); what's built is
[asr_provider_implementation_plan.md](asr_provider_implementation_plan.md).
`benchmarks/run_benchmark.py` runs and scores one configuration;
`scripts/bench_status.py` shows what's been scored against what. The whole
procedure, from annotation to reported number, is
[asr_evaluation_methods.md](asr_evaluation_methods.md). Expect
higher error rates than published numbers (medical vocabulary, overlapping
speech, multi-party rooms): that gap is a finding; report it.

### 8a. Gold references and frozen audio

Gold is the expensive, essential part:
`benchmarks/references/<recording>.gold.txt` (and `.gold.rttm`), exported from an
adjudicated `.eaf` by `scripts/eaf_to_gold.py`, never typed by hand, with a
`.gold.json` saying how its pass 1 was made. **Blind** gold (route A) can
score any engine. **Seeded** gold (route B: pass 1 corrected from the
`whisperx_disfluent`/`large-v3` transcript; `multidata.seeding`) leans toward
that engine, so `run_benchmark.py` refuses to score it there, every ledger
row records which kind of gold it used (`gold_pass1`), and
`bench_status.py` reports the two kinds separately
([transcription_standards.md](transcription_standards.md) §9). The audio a
benchmark scores must never change; its hash is recorded with every run.

### 8b. Metrics

| Question | Metric | Tool |
|---|---|---|
| Transcription accuracy | WER, CER | `jiwer` |
| Speaker attribution | DER, JER | `pyannote.metrics` (not wired yet, plan Phase 6.4) |
| Word timing | boundary error vs gold | not built |

Names are redacted from both sides, then `normalize.py`'s L0 verbatim
profile gives `wer`/`cer` and its L1 filler-neutral profile gives `wer_l1`
([asr_evaluation_methods.md](asr_evaluation_methods.md) §4).

### 8c. Sweep design

Vary **one axis at a time**, everything else pinned: model size, engine,
VAD, speaker-count hints, prompt, audio preparation (raw vs. normalized).
Engine responses are cached (`multidata.bench_runs`), so a repeated
identical run costs nothing.

### 8d. Provenance

Every run appends a row to `benchmarks/results/runs.csv` (append-only, in
git): engine, model, audio hash, scores, scoring method, gold hash, commit,
wall time, machine; the full config and normalized output go in
`benchmarks/runs/<run_id>/` (gitignored, since it holds transcript text). A
result that can't be traced to an exact configuration is noise.

---

## 9. Pose (`run_stage.py pose`, md-pose, GPU)

`pose.py` runs rtmlib's `BodyWithFeet` models: a YOLOX-m person detector and
RTMPose-m trained on **HALPE_26** (the 17 COCO body joints in COCO order, then
head top, neck, mid-hip and six foot points; `pose.KEYPOINTS`). Only five
points are on the face, deliberately (IRB).

Output per camera, `data/pose/<recording>/<camera>.npz`: keypoints `(M, T, 26, 2)`,
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
| Stretches per video (times, view id, match quality, move from previous) | `data/views/<recording>/<camera>.json` |
| View library: 5 sample frames per view (`.npz`), same as a strip (`.png`) | `data/views/library/<camera>/v<n>.*` |
| One row per view: `label`, `label_source` (`provisional`/`confirmed`), `notes` | manifest `camera_views` |
| Seconds per video per view | manifest `video_views` |

A view is **recurring** when it shows up in 2+ recordings or 3+ separate times
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

After new recordings go through `views`, repeat from step 1: new views appear with
no label. The first 65 recurring views were labelled provisionally by eye on
2026-11-01 (`scripts/label_views_2026-11-01.py`, confidence in each note).

---

## 9b. Camera sync (`run_stage.py sync`, md-speech, CPU)

To pair the two cameras' frames in time (for 3D, and for demo clips):

- **A recording's two files start up to ~1 s apart, by a fixed amount, with no
  drift.** Both carry the same audio (§5), so cross-correlating it (GCC-PHAT
  over 10 s windows every 30 s) gives the offset. Stored as
  `videos.sync_offset_s`: seconds to add to this file's time to get the
  reference camera's time (the audio camera, offset 0), with
  `videos.sync_agreement` (share of windows agreeing). When windows disagree
  the offset stays NULL and the row is `failed`; it is never guessed.
- **Files drop frames**, sometimes minutes of them, so frame number is not
  time. The stage stores every frame's presentation time in
  `data/sync/<recording>/<camera>.npz`.

A pose frame `i` happened at `frame_ms[i] / 1000 + sync_offset_s` on the
reference clock (pose stores the time of every frame it read, so this works
even where OpenCV skipped frames; `sync.pose_reference_times()`).
`sync.nearest_frames()` pairs two cameras' frames by time.

Known failures: room 11 (camera 31's audio has gaps), 261041/41, and very
short clips.

---

## 10. The manifest

`manifest.sqlite` (SQLite, gitignored, written only on the processing host). All database
code is in `manifest.py`, which imports only the standard library so it works
in every env. Additive column migrations run automatically on connect.

| Table | One row per | Notes |
|---|---|---|
| `recordings` | encounter, from the source system's export | `recording_id` is the export's sheet title. Holds real names (learner, SP, preceptor): never print them. `audio_camera`, `cloud_release`, `consent_ref` live here, and the LearningSpace links (`ls_*`, `recording_start_utc`) |
| `videos` | camera file | what every stage reads and writes: file facts, `<stage>_status` per stage, `sync_offset_s`; LearningSpace's `ls_file_id`, `size_bytes`, `download_url` |
| `cameras` | camera | hand-verified camera -> room (§2); seeded from `db_seed/camera_rooms.csv` |
| `file_matches` | raw file name | cache of recording matching: `matched`, `ambiguous`, `no_match`, `no_room` |
| `gold` | gold reference | who annotated, when frozen, which span |
| `stage_runs` | stage run on one video | wall time, media time, realtime factor, host, GPU, commit, flags, stage details, error; feeds [cost_estimate.md](cost_estimate.md) |
| `camera_views`, `video_views` | view; video x view | §9a |
| `speaker_prosody` | diarized speaker in a recording | §6b |
| `recording_scores` | checklist part, section and submission | the faculty observer's points: the recording's grade ([learningspace.md](learningspace.md)) |
| `ls_rooms` | LearningSpace room GUID | its room number, learned from linked recordings |

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

- **Cost:** about 39 machine-minutes and 0.56 GB per recording, 90% of the compute
  being 2D pose; the full small-room catalogue doesn't fit on the pilot
  workstation's main drive. Details and scenarios: [cost_estimate.md](cost_estimate.md).
- **What's irreplaceable:** the manifest and the gold/annotation `.eaf`
  files. Raw video can be re-exported from the recording system; everything
  else regenerates. Backups: [operations.md](operations.md).
- **Consent and cloud release are pipeline fields:** `recordings.consent_ref` and
  `recordings.cloud_release`. No third-party ASR call may happen for a recording without
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

# stages, in order (each processes only its pending rows; --only <recording> for one recording)
conda activate md-speech
python scripts/run_stage.py audio
python scripts/run_stage.py asr         # --engine whisperx --model large-v3 to vary
python scripts/run_stage.py prosody     # before elan: the ELAN file gets its pitch tier
python scripts/run_stage.py elan
python scripts/run_stage.py sync
conda activate md-pose
python scripts/run_stage.py views       # one worker
python scripts/run_stage.py pose --accel-device cuda --shard 0/2   # and 1/2 alongside

# name camera views (md-pose for sheets)
python scripts/review_views.py sheets | export | import [--write]

# demo clip of a recording (md-pose)
python scripts/demo_clip.py 261498

# benchmarking (md-speech)
python scripts/eaf_to_gold.py --eaf <path.eaf> --recording <recording_id>
python benchmarks/run_benchmark.py --recording <recording_id> --audio data/audio/<recording_id>/<camera>.wav \
    --engine whisperx_disfluent --model medium
python scripts/bench_status.py
```
