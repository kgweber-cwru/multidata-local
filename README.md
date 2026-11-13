# multidata-local

Turns video of clinical teaching encounters into research data. The
recordings are simulated sessions: a learner, a simulated patient (an actor) and
sometimes a preceptor, filmed by two pan/tilt/zoom cameras per exam room
with one shared microphone feed. From each recording the pipeline produces:

- **transcripts** with word timing and who spoke (Whisper + pyannote), and
  ELAN files for annotators;
- **prosody**: pitch, loudness and voice quality per speaker;
- **camera sync**, and **camera views** (when each camera moves, and which
  operator preset it's on);
- **2D body pose** per camera, heading toward 3D.

There's also an **ASR benchmarking** wing, scoring speech engines against
hand-made gold transcripts. During the pilot everything runs on one Linux workstation with an RTX 5080
(`tofino`); afterwards it moves to a high-performance cluster.

> ⚠️ **Identifiable human-subjects data.** The patient is an actor, but
> learners and preceptors are real people who say their names, so
> transcripts and the manifest's `recordings` table carry real names. Never print
> names in shared output, and nothing goes to a third-party ASR service
> without `recordings.cloud_release`, a check that is **not enforced in code
> yet** ([docs/status.md](docs/status.md)).

## Where to read next

| If you want to... | Read |
|---|---|
| know where the project stands and what's decided | [docs/status.md](docs/status.md) |
| learn the pipeline hands-on | [notebooks/ingest_walkthrough.ipynb](notebooks/ingest_walkthrough.ipynb) |
| understand a stage (inputs, outputs, why) | [docs/pipeline.md](docs/pipeline.md) |
| use an output file (fields, units, clocks, how to read it) | [docs/data_formats.md](docs/data_formats.md) |
| run, watch or stop a long job; backups | [docs/operations.md](docs/operations.md) |
| know what processing costs per recording | [docs/cost_estimate.md](docs/cost_estimate.md) |
| work on pose / 3D | [docs/pose_3d.md](docs/pose_3d.md) |
| understand how gold is made and engines are scored (methods) | [docs/asr_evaluation_methods.md](docs/asr_evaluation_methods.md) |
| annotate a gold transcript | [docs/annotator_guide.md](docs/annotator_guide.md), with the rules in [docs/transcription_standards.md](docs/transcription_standards.md) |
| coordinate annotation (kits, returns, gold, scoring) | [annotation/README.md](annotation/README.md) |
| work on ASR benchmarking code | [docs/asr_provider_spec.md](docs/asr_provider_spec.md), [docs/asr_provider_implementation_plan.md](docs/asr_provider_implementation_plan.md) |
| run or write tests | [tests/README.md](tests/README.md) |

## Words used throughout

| Term | Meaning |
|---|---|
| **recording** | one recorded encounter: one learner, one case, one room (LearningSpace's *video recording*). `recording_id` is the six-digit number from LearningSpace's export; `ls_recording_id` is its LearningSpace GUID |
| **activity / event / case** | LearningSpace's words: an *activity* is an exam series ("CSE 2B Short Station (2026)"), an *event* one scheduled session of it, a *case* the scenario the learner works through ([docs/learningspace.md](docs/learningspace.md)) |
| **camera / video** | each recording has (usually) two camera files; outputs are keyed `(recording_id, camera)` |
| **manifest** | `manifest.sqlite`: every recording and video, and each stage's status per video ([docs/pipeline.md](docs/pipeline.md) §10) |
| **stage** | one processing step (`audio`, `asr`, `pose`, ...), run by `scripts/run_stage.py <stage>` over every video still `pending` for it |
| **audio camera** | the one camera per recording whose file the audio stages use; both carry the same sound |
| **reference clock** | the audio camera's timeline; `sync` gives the other camera's offset from it |
| **stretch / view / preset** | a stretch is a period a camera holds still; a view is where it's pointing; recurring views are the operators' presets |
| **slot** | one detected person in one frame of pose output; *not* the same person across frames |
| **gold** | a hand-made, adjudicated reference transcript for scoring ASR; pass 1 is either *blind* or *seeded* from a fixed machine draft |

## Layout

```
src/multidata/  the package: one module per stage plus helpers (see its __init__.py)
scripts/        run_stage.py       run any stage over the manifest
                ingest_raw.py      register new raw videos
                review_views.py    contact sheets + spreadsheet to name camera views
                demo_clip.py       demo video of a recording: both cameras, pose, faces blurred
                sample_pose_overlays.py   pose drawn on whole videos, for quality checks
                test_detect_every.py      does detecting every Nth frame change pose?
                eaf_to_gold.py     adjudicated pass 2 -> gold reference
                bench_status.py    gold coverage and scores at a glance
                label_views_2026-11-01.py one-off: the first provisional view labels
                backup_to_raid.sh  nightly backup (cron)
notebooks/      ingest_walkthrough.ipynb (start here); the others are personal scratch
benchmarks/     ASR benchmarking: run_benchmark.py, configs/, references/ (gold),
                results/ (runs.csv); runs/ and cache/ are gitignored
annotation/     gold-annotation kits (via Box) and who has which pass
elan/           the versioned ELAN template for gold annotation
db_seed/        git-tracked copy of the hand-verified camera -> room table
env/            conda environments (see below)
docs/           the guides above
tests/          pytest
data/, logs/, manifest.sqlite   GITIGNORED: video, every output, logs, the registry
```

## Getting started

```bash
# environments: speech and pose stacks are kept apart on purpose
conda env create -f env/utility.yml        # md-utility: notebooks, Excel import, ingest
conda env create -f env/speech.yml         # md-speech:  audio, asr, elan, prosody, sync, benchmarks
conda env create -f env/pose-nvidia.yml    # md-pose:    pose, views, demo clips (Linux/CUDA;
                                           #             env/pose.yml on a Mac)
conda activate md-pose && pip install --force-reinstall --no-deps "onnxruntime-gpu[cuda,cudnn]"
                                           # Linux only, required: see top of env/pose-nvidia.yml
for e in md-utility md-speech md-pose; do conda run -n $e pip install -e .; done
huggingface-cli login                      # pyannote is gated; accept its model terms

# new videos -> processed recordings (or follow the walkthrough notebook)
python scripts/ingest_raw.py --write                                 # md-utility
conda activate md-speech
for s in audio asr prosody elan sync; do python scripts/run_stage.py $s; done
conda activate md-pose
python scripts/run_stage.py views
python scripts/run_stage.py pose --accel-device cuda                 # hours: see docs/operations.md

# a demo for someone
python scripts/demo_clip.py 261498                                   # md-pose

# tests
conda run -n md-speech python -m pytest -q
conda run -n md-pose python -m pytest -q tests/test_pose_io.py tests/test_views.py
```
