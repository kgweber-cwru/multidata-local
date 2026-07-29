# multidata-local

Clean-room prototype of the multidata pipeline on the CWRU Mac mini (Apple
Silicon): from raw clinical-interaction video → **speech transcripts, timing,
diarization, ELAN, pose estimation**, plus an **ASR benchmarking** wing.

Deliberately *not* a fork of the thrashed `multidata` repo — only known-good code
is carried over.

## Start here

1. **[multidata_local_pipeline.md](multidata_local_pipeline.md)** — the full
   build & run guide (machine prep, envs, every stage, scaling to 300–400 videos).
2. **[docs/gold_annotation_guide.md](docs/gold_annotation_guide.md)** — how to
   build gold references for benchmarking on *our* audio.

## Layout

```
env/            conda envs — speech (ASR) and pose, kept separate on purpose
src/multidata/  stages: manifest, ingest, audio, asr, diarize, elan, pose
                helpers: kinematics (pose features), acoustics (Praat features)
scripts/        run_stage.py — manifest-driven, resumable batch runner
benchmarks/     ASR benchmarking wing (references / configs / results)
docs/           guides
data/           GITIGNORED — raw video + derived artifacts (see pipeline doc §2)
logs/           GITIGNORED — run_stage.py failure traces
```

## Quick start

```bash
# environments (see pipeline doc §3)
conda env create -f env/utility.yml    # md-utility: manifest, Excel import, notebooks
conda env create -f env/speech.yml     # md-speech:  audio, asr, diarize, elan, benchmarks
conda env create -f env/pose.yml       # md-pose:    rtmlib pose
huggingface-cli login                  # pyannote is gated — accept model terms

# install the multidata package into each env (once per env — see pipeline doc §3)
conda activate md-utility && pip install -e .
conda activate md-speech  && pip install -e .
conda activate md-pose    && pip install -e .

# run a stage across all pending manifest rows
conda activate md-speech && python scripts/run_stage.py audio
conda activate md-speech && python scripts/run_stage.py asr      # --engine whisperx
conda activate md-pose   && python scripts/run_stage.py pose
```

## Status

Every module the pipeline doc calls for now exists, but **maturity varies a lot
by module** — know which is which before trusting a run:

| Module | State |
|---|---|
| `pose.py`, `kinematics.py` | Promoted verbatim from `mmpose.ipynb`; **ran on real video** on the old machine |
| `elan.py` | Promoted from `transcription_suite.ipynb`; **ran on real transcripts**, any engine |
| `acoustics.py` | Promoted from `praat_maker.ipynb`; **ran on real audio** |
| `ingest.py`, `audio.py`, `manifest.py`, `run_stage.py` | **Ran end-to-end on real cases** — ingest, audio, asr, elan stages all exercised |
| `asr.py`, `diarize.py` | **Ran end-to-end on real cases.** Default engine is `faster_whisper` (`medium`, `language=en`); every engine's output is word-level speaker-labeled, diarization reused via cached RTTM rather than recomputed. `whisperx` available via `--engine` for testing. |
| `run_benchmark.py` | Written from doc §8; blocked on gold references existing |

There are no tests yet. See pipeline doc §11 for the 1 → few → scale phasing.

**ASR direction:** Whisper + pyannote run locally. Transcription Suite (the old
machine's HTTP server) survives only as `--engine suite`, a benchmark comparator;
delete it from `asr.py` if you'd rather it not be there.

> ⚠️ Clinical / PHI data. See pipeline doc §11 and the gold guide §6 before moving
> real data onto this machine.
