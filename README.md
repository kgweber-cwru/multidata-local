# multidata-local

Clean-room prototype of the multidata pipeline on the CWRU Mac mini (Apple
Silicon): from raw clinical-interaction video → **speech transcripts, timing,
diarization, ELAN, pose estimation**, plus an **ASR benchmarking** wing.

Deliberately *not* a fork of the thrashed `multidata` repo — only known-good code
is carried over.

## Start here

0. **[docs/current_status.md](docs/current_status.md)** — picking this up
   cold (new session, new contributor)? Read this one first: what's real vs.
   built-but-unproven, active threads, the one open blocking question.
1. **[multidata_local_pipeline.md](multidata_local_pipeline.md)** — the full
   build & run guide (machine prep, envs, every stage, scaling to 300–400 videos).
2. **[docs/transcription_standards.md](docs/transcription_standards.md)** — the
   conventions every gold reference obeys (policy; versioned).
3. **[docs/annotator_guide.md](docs/annotator_guide.md)** — step-by-step ELAN
   walkthrough for producing one gold transcript.
4. **[docs/asr_provider_spec.md](docs/asr_provider_spec.md)** — how local and
   cloud ASR providers plug in, and how the selection bake-off is scored.
5. **[docs/asr_provider_implementation_plan.md](docs/asr_provider_implementation_plan.md)**
   — the build checklist for the spec above: what's done, what's deliberately
   deferred, and why.
6. **[docs/running_job_notes.md](docs/running_job_notes.md)** — PIDs, log
   paths, and check-in commands for whatever batch job is actually running
   right now (currently: pose, split across this Mac and a Linux CUDA box).

## Layout

```
env/            conda envs — speech (ASR) and pose, kept separate on purpose;
                pose has two variants (pose.yml Mac / pose-nvidia.yml Linux+CUDA)
                that both build the same md-pose env name -- see Quick start
src/multidata/  stages: manifest, ingest, audio, asr, diarize, elan, pose
                helpers: kinematics (pose features), acoustics (Praat features)
                ASR benchmark wing: normalize, redact, records, providers,
                glossary, bench_runs (see docs/asr_provider_spec.md)
scripts/        run_stage.py — manifest-driven, resumable batch runner
                eaf_to_gold.py — hand-annotated .eaf -> a scoreable gold reference
                bench_status.py — one-screen view of gold coverage + scores
benchmarks/     ASR benchmarking wing
                configs/    providers.yaml, glossary.yaml — the capability/term registries
                references/ tracked gold references (<case_id>.gold.txt/.rttm)
                results/    tracked runs.csv (append-only provenance ledger)
                runs/, cache/  GITIGNORED — per-run detail + response cache (see spec §7/§9)
elan/           template.etf — the versioned annotation template
docs/           guides
tests/          pytest — see tests/README.md for what is/isn't covered
data/           GITIGNORED — raw video + derived artifacts (see pipeline doc §2)
logs/           GITIGNORED — run_stage.py's structured log + nohup/PID files
                for whatever's running now (see docs/running_job_notes.md)
```

## Quick start

```bash
# environments (see pipeline doc §3)
conda env create -f env/utility.yml    # md-utility: manifest, Excel import, notebooks
conda env create -f env/speech.yml     # md-speech:  audio, asr, diarize, elan, benchmarks

# md-pose builds differently depending on the box -- same env name, different
# onnxruntime backend, because there's no single onnxruntime wheel that covers
# both CoreML (Mac) and CUDA (Linux/Nvidia). Pick ONE of these two, matching
# the machine you're on:
conda env create -f env/pose.yml         # Mac (Apple Silicon):  onnxruntime, CoreML EP
conda env create -f env/pose-nvidia.yml  # Linux (Nvidia/CUDA):  onnxruntime-gpu[cuda,cudnn]

# Linux/CUDA only, REQUIRED after the pose-nvidia.yml create: rtmlib hard-
# depends on plain (CPU) onnxruntime regardless of what's requested above, and
# it silently wins the shared onnxruntime/ install path more often than not.
# See the comment at the top of env/pose-nvidia.yml before assuming CUDA works.
conda activate md-pose && pip install --force-reinstall --no-deps "onnxruntime-gpu[cuda,cudnn]"

huggingface-cli login                  # pyannote is gated — accept model terms

# install the multidata package into each env (once per env — see pipeline doc §3)
conda activate md-utility && pip install -e .
conda activate md-speech  && pip install -e .
conda activate md-pose    && pip install -e .

# run a stage across all pending manifest rows
conda activate md-speech && python scripts/run_stage.py audio
conda activate md-speech && python scripts/run_stage.py asr      # --engine whisperx
conda activate md-pose   && python scripts/run_stage.py pose

# tests (md-speech only; everything else has no pytest/pyyaml installed)
conda activate md-speech && python -m pytest -q

# once you have a gold .eaf: export it, then score an engine against it
conda activate md-speech && python scripts/eaf_to_gold.py --eaf <path.eaf> --case <case_id>
conda activate md-speech && python benchmarks/run_benchmark.py --case <case_id> \
    --audio data/audio/<case_id>/<camera>.wav --engine whisperx_disfluent --model medium
conda activate md-speech && python scripts/bench_status.py   # what's scored, what's missing
```

## Status

Every module the pipeline doc calls for now exists, but **maturity varies a lot
by module** — know which is which before trusting a run:

| Module | State |
|---|---|
| `pose.py`, `kinematics.py` | **Settled and running now** (commit `aaf0a9b`): `rtmlib.Wholebody` (133 keypoints), device auto-detect (mps/cuda), `--detect-every` frame-skip, `--shard` for concurrent workers. Split across this Mac and a Linux CUDA box (`tofino`) — see pipeline doc §9 and `docs/running_job_notes.md` |
| `elan.py` | Promoted from `transcription_suite.ipynb`; **ran on real transcripts**, any engine |
| `acoustics.py` | Promoted from `praat_maker.ipynb`; **ran on real audio** |
| `ingest.py`, `audio.py`, `manifest.py`, `run_stage.py` | **Ran end-to-end on real cases** — ingest, audio, asr, elan stages all exercised |
| `asr.py`, `diarize.py` | **Ran end-to-end on real cases.** Default engine is `faster_whisper` (`medium`, `language=en`); every engine's output is word-level speaker-labeled, diarization reused via cached RTTM rather than recomputed. `whisperx` (hallucination-avoidance-tuned) and `whisperx_disfluent` (tuned to *keep* disfluencies — see [asr_provider_spec.md](docs/asr_provider_spec.md)) available via `--engine`. |
| `eaf_to_gold.py`, `run_benchmark.py`, `bench_status.py` | **Ran end-to-end on a real case** (261456) — export, live/cached/cache-hit scoring, excerpt-span filtering, and the status view all exercised, not just sketched. First real comparison already in: `whisperx_disfluent` (WER .29) beat `whisperx` (WER .41) on that one case — one data point, not yet a pattern. |
| `normalize.py`, `redact.py`, `records.py`, `providers.py`, `glossary.py`, `bench_runs.py` | New this round, pure/tested modules underlying the above — see [asr_provider_spec.md](docs/asr_provider_spec.md) |

**178 tests pass** (`python -m pytest -q` in `md-speech`) — see `tests/README.md`
for what is and isn't covered. See pipeline doc §11 for the 1 → few → scale
phasing.

**ASR direction:** Whisper + pyannote run locally. Transcription Suite (the old
machine's HTTP server) survives only as `--engine suite`, a benchmark comparator;
delete it from `asr.py` if you'd rather it not be there.

> ⚠️ **Identifiable human-subjects data.** Today's corpus is OSCE
> simulated-patient encounters — the "patient" is an actor, so there is no
> patient PHI — but the learner and preceptor are real people, and they
> self-identify verbally, so transcripts carry real names. Nothing leaves this
> machine for a third-party ASR provider without `cases.cloud_release`. See
> pipeline doc §11, [transcription_standards.md](docs/transcription_standards.md) §8,
> and [asr_provider_spec.md](docs/asr_provider_spec.md) §9.
