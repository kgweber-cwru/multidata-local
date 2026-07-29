# Multidata Local Pipeline — Build & Run Guide (Mac mini, Apple Silicon)

A clean-room rebuild of the multidata pipeline on the loaned Mac mini, escaping
the current `multidata` env mess. Goal: from raw clinical-interaction videos,
produce **speech transcripts, timing, diarization, ELAN files, and pose
estimation**, plus a wing for **ASR benchmarking** — reproducibly, and scalable
from 1 → a few → potentially 300–400 videos.

> **Assumptions baked in** (from setup decisions): Apple Silicon (arm64),
> miniforge/conda, Whisper + pyannote run directly (no TranscriptionSuite),
> manual video ingest now with a scripted-pull stub for later.

---

## 0. Design principles (why the layout below looks the way it does)

1. **Raw is immutable.** Source videos land once, are never edited in place, and
   everything else is *derived* and regenerable. This is what saves you when the
   pipeline changes (and it will).
2. **Stages are separable and resumable.** Ingest → audio → ASR → diarize → ELAN
   → pose are independent steps that read/write files and record status in a
   manifest, so you can re-run one stage for one video without redoing the rest.
3. **Two environments, not one.** The current mess came from cramming
   incompatible ML stacks together. We split **speech** (torch/whisper/pyannote)
   from **pose** (rtmlib/onnxruntime) so their dependency graphs never fight.
4. **Everything is provenanced.** Model name + version + params + input hash get
   recorded with each output. Non-negotiable once benchmarking starts.

---

## 1. Machine prep (one-time)

```bash
# Xcode command line tools (compilers, git)
xcode-select --install

# Homebrew (if not present) — https://brew.sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# System-level media + convenience tools
brew install ffmpeg git git-lfs wget

# miniforge = clean arm64 conda (do NOT reuse the old miniconda)
brew install miniforge
conda init zsh
# restart the shell, then:
conda config --set auto_activate_base false
```

Verify you're truly arm64 (not Rosetta):

```bash
uname -m          # -> arm64
python3 -c "import platform; print(platform.machine())"   # -> arm64
```

- Install tailscale
- No SSH allowed: install VSCode and VSCode tunnel
- Other `homebrew` packages: `huggingface-cli`; `npm` (for sqltools extension)

---

## 2. New repo scaffold

Fresh repo 

```bash
mkdir -p ~/projects/multidata-local && cd ~/projects/multidata-local
git init
git lfs install
```

Proposed structure:

```
multidata-local/
├── README.md
├── multidata_local_pipeline.md        # this document
├── pyproject.toml                      # `pip install -e .` per env (§3)
├── env/
│   ├── speech.yml                      # conda env: ASR + diarization + ELAN
│   └── pose.yml                        # conda env: rtmlib pose
├── src/multidata/
│   ├── __init__.py
│   ├── manifest.py                     # the video registry (see §10)
│   ├── ingest.py                       # §4  copy/validate raw video
│   ├── casematch.py                    # §2/§4  filename -> case_id resolution
│   ├── audio.py                        # §5  extract + normalize audio
│   ├── asr.py                          # §6  transcription + word timing
│   ├── diarize.py                      # §6  pyannote diarization -> RTTM
│   ├── elan.py                         # §7  build .eaf
│   ├── pose.py                         # §9  rtmlib pose extraction
│   ├── kinematics.py                   #     rule-based pose features
│   └── acoustics.py                    #     Praat/parselmouth features
├── scripts/
│   └── run_stage.py                    # CLI: run one stage over the manifest
├── benchmarks/                         # §8  ASR benchmarking wing
│   ├── references/                     # gold human transcripts
│   ├── configs/                        # model/param sweeps
│   ├── results/                        # metrics output (git-tracked)
│   └── run_benchmark.py
├── data/                               # GITIGNORED — large + sensitive
│   ├── raw/                            # immutable source, flat (no per-case dirs — §2)
│   ├── room_camera_map.csv             # hand-verified camera_id -> room (§2)
│   ├── audio/<case_id>/
│   ├── transcripts/<case_id>/
│   ├── diarization/<case_id>/       # RTTM, for DER scoring (§8b)
│   ├── elan/<case_id>/
│   ├── praat/<case_id>/
│   └── pose/<case_id>/
├── logs/                               # GITIGNORED — run_stage.py failure traces
├── manifest.sqlite                     # cases + videos, sqlite3 (§10)
└── .gitignore
```

`.gitignore` (critical — keep data and secrets out of git):

```gitignore
data/
logs/
*.mp4
*.mp3
*.wav
*.pkl
manifest.sqlite
.env
__pycache__/
*.eaf.bak
.DS_Store
```

### case / camera naming convention

`data/raw/` is a **flat** directory — one file per camera per recorded
encounter, no per-case subfolder:

```
data/raw/
├── 20260224-132704-v308933-41.mp4
├── 20260224-132704-v308934-10.mp4
└── ...
```

Raw filenames encode: **`<date:YYYYMMDD>-<time:HHMMSS>-<recording_id>-<camera>.mp4`**.
E.g. `20260224-132704-v308933-41.mp4` is date `2026-02-24`, time `13:27:04`,
recording_id `v308933` (the source system's internal video number, kept for
provenance), camera `41`.

**The filename does not contain a case_id.** The second field looks like it
could be one (it's what an earlier version of this doc — and the code —
assumed), but it's a timestamp: the time the recording started, to the
second. There is no field anywhere in the raw filename that identifies the
case.

To find which case a file belongs to, you have to match it against the
`cases` table on two things (`src/multidata/casematch.py`):

1. **timestamp**, at **minute** granularity only — the case's
   `recording_start_time` (from the Excel import) is truncated to `:00`
   seconds, so a video's `HH:MM:SS` can only be compared after zeroing its
   seconds too.
2. **room, via camera** — minute-level timestamps aren't unique by
   themselves (two rooms can start recording in the same minute), so the
   camera's room disambiguates. Camera→room isn't in any metadata export; it's
   discovered by hand (pull a few videos per room, confirm which camera you're
   looking at) and recorded in `data/room_camera_map.csv`
   (`room,camera_id`), extended as more pairs are confirmed. Do not try to
   infer this mapping automatically from the video timestamps — it's exactly
   as ambiguous as the case match it's supposed to disambiguate.

`casematch.resolve(path)` runs that match and caches the result (success or
failure) in the manifest's `video_case_matches` table, so it's a one-time cost
per file, not a per-run rescan; unresolved files (`no_room` / `no_match` /
`ambiguous`) surface via `manifest.unresolved_matches()` for you to fix by
hand (extend the camera map, or check the source spreadsheet) and re-run.

Every downstream artifact is keyed on `(case_id, camera)`, not the raw
filename. `ingest.summarize()` parses the filename and folds `camera`,
`recording_id`, `video_date`, `video_time` into the same dict as the
ffprobe/hash facts; `casematch.resolve()` supplies the `case_id` separately —
`manifest.add_video(case_id=casematch.resolve(path), **ingest.summarize(path))`
registers a video in one call. Artifacts mirror the `(case_id, camera)` grain:
`data/audio/<case_id>/41.wav`, `data/pose/<case_id>/41.pkl`, where `case_id`
is a real case_id — an Excel sheet title, e.g. `252848` — never the
filename's time field (`132704` in the example above), despite the
coincidental resemblance that misled earlier versions of this doc.

---

## 3. Environments

### Utility/Orchestration (`env/utility.yml`)

```yaml
name: md-utility
channels: [conda-forge]
dependencies:
  - python=3.11
  - ffmpeg
  - pip
  - pip:
      - jupyter
      - openpyxl
      - sqlalchemy
      - requests
      - soundfile 
      - librosa 
      - numpy 
      - pandas
```

```bash
conda env create -f env/utility.yml
conda activate md-utility
```


### Speech env (`env/speech.yml`)

```yaml
name: md-speech
channels: [conda-forge]
dependencies:
  - python=3.11
  - ffmpeg
  - pip
  - pip:
      - torch torchaudio            # arm64 wheels; MPS-capable
      - openai-whisper              # reference implementation (benchmark baseline)
      - faster-whisper              # CT2 backend, fast CPU int8 (portable baseline)
      - mlx-whisper                 # Apple-Silicon-native, fastest here
      - whisperx                    # whisper + word alignment + diarization glue
      - pyannote.audio              # diarization
      - jiwer                       # WER/CER for benchmarking
      - pyannote.metrics            # DER for benchmarking
      - pympi-ling                  # ELAN .eaf read/write
      - soundfile librosa numpy pandas
```

```bash
conda env create -f env/speech.yml
conda activate md-speech
```

**Notes for Apple Silicon:**
- `faster-whisper` (CTranslate2) has **no Metal/MPS** support → runs CPU int8.
  Still fine, and it's your stable, portable benchmark baseline.
- `mlx-whisper` runs natively on the GPU via MLX → your **fast** path for bulk
  work. Keep both: MLX for throughput, faster-whisper for a reproducible anchor.
- `pyannote` on MPS is improving but occasionally flaky; if you hit NaNs/crashes,
  force `torch.device("cpu")` — diarization is not the bottleneck.

### Pose env (`env/pose.yml`) — kept separate on purpose

```yaml
name: md-pose
channels: [conda-forge]
dependencies:
  - python=3.11
  - pip
  - pip:
      - rtmlib
      - onnxruntime                 # CPU; CoreML EP optional (see §9)
      - mmengine                    # for the .pkl dump format you already use
      - opencv-python numpy
```

```bash
conda env create -f env/pose.yml
```

> We deliberately do **not** install full mmpose/mmcv — rtmlib + onnxruntime
> gives you RTMPose without the mmcv build hell that helped make the old env a
> mess. Reuse your existing `Body`/`Wholebody` cells.

### Install `multidata` itself — once per env

`src/multidata/` is an installable package (`pyproject.toml` at the repo
root, src-layout). After creating **each** of the three envs above, activate
it and install `multidata` into it, editable:

```bash
cd ~/projects/multidata-local        # repo root — where pyproject.toml lives
conda activate md-utility && pip install -e .
conda activate md-speech  && pip install -e .
conda activate md-pose    && pip install -e .
```

This has to happen **once per env**, not once total — each conda env has its
own isolated `site-packages`, so an editable install in `md-speech` doesn't
make `multidata` importable in `md-pose`. Editable (`-e`) means it links back
to `src/multidata/` rather than copying, so edits show up immediately with no
reinstall.

`pyproject.toml` deliberately declares **no dependencies** — those already
live in the three `env/*.yml` files above, split on purpose so the incompatible
ML stacks don't collide (§0 principle 3). Listing them in `pyproject.toml` too
would make `pip install -e .` try to pull all three stacks into every env,
which is exactly what the env split exists to avoid.

Once installed, `import multidata` works from anywhere — any working
directory, any script, any Jupyter kernel pointed at one of these envs — no
`sys.path` hacking needed. (`scripts/run_stage.py` and
`benchmarks/run_benchmark.py` also do a manual
`sys.path.insert(0, str(ROOT / "src"))` as a belt-and-suspenders fallback, so
they still work even before you've run the install above — but for notebooks
there's no equivalent fallback, so do the install first.)

### Hugging Face access (needed for pyannote)

pyannote's pipelines are gated. Once:

```bash
huggingface-cli login          # paste a read token from hf.co/settings/tokens
```

Then accept the model conditions in the browser for
`pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`. Store the
token in `.env` (gitignored), never in code.

---

## 4. Video ingest

### 4a. Manual (now)

Drop source files into `data/raw/` (flat, no per-case subfolder — see §2),
named to the §2 spec (`<date>-<time>-<recording_id>-<camera>.mp4`), then
resolve the case_id, register, and validate with `src/multidata/ingest.py`
and `src/multidata/casematch.py`:

```python
>>> from multidata import casematch, ingest, manifest
>>> path = "data/raw/20260224-132704-v308933-41.mp4"
>>> case_id = casematch.resolve(path)   # matches timestamp+room against cases; caches the result
>>> info = ingest.summarize(path)       # camera/recording_id/video_date/video_time + ffprobe/hash facts
>>> manifest.add_video(case_id=case_id, **info)   # case must already exist
```

`ingest.parse_filename()` does the naming-convention check (it no longer
touches case_id — the filename doesn't have one); `ingest.probe()` +
`ingest.sha256()` do the rest. `casematch.resolve()` raises `CaseMatchError`
when a file can't be resolved to exactly one case — check
`manifest.unresolved_matches()` to see everything waiting on a manual look
(usually a camera missing from `data/room_camera_map.csv`). See
`src/multidata/ingest.py` and `src/multidata/casematch.py` for the real
implementation (this doc doesn't duplicate it — code drifts, docs rot).

Ingest checklist per case:
- [ ] Files named to convention, correct camera numbers — `ingest.parse_filename()`
      raises if not.
- [ ] Case resolved — `casematch.resolve()` finds exactly one case for the
      file's (timestamp, room); anything else lands in
      `manifest.unresolved_matches()` instead of guessing.
- [ ] `ffprobe` confirms duration, fps, resolution, and that **audio exists** on
      at least one camera (which one? record it — that's your ASR source).
- [ ] Frame counts / durations across cameras are consistent (sync sanity).
- [ ] `sha256` recorded in the manifest (detects silent corruption/duplication).
- [ ] The case itself already exists in the manifest (import the source-system
      Excel export first — §10) — `manifest.add_video` refuses videos for a
      case it doesn't know about.

### 4b. Scripted pull (STUB — later)

> **TODO / not yet built.** If the source system's API can be reverse-engineered:
> 1. Inspect network calls in the browser dev-tools (or `mitmproxy`) while
>    downloading one video manually; capture the auth flow and the media URL
>    pattern.
> 2. Replicate as a small `ingest_remote.py` that lists encounters and pulls to
>    `data/raw/`, reusing the same validation as 4a.
> 3. Rate-limit and cache; treat the source as read-only and fragile.
>
> Do not build this until the manual path works end-to-end for a handful of
> cases — you don't want to debug ingest and processing at the same time.

---

## 5. Audio preparation for transcription

Whisper and pyannote both want **16 kHz mono WAV**. Extract from whichever camera
carries the cleanest audio (recorded in §4).

```bash
ffmpeg -i data/raw/132704/20260224-132704-v308933-41.mp4 \
       -vn -ac 1 -ar 16000 -sample_fmt s16 \
       data/audio/132704/41.wav
```

Optional but often worth it for messy clinical rooms:
- Loudness-normalize: `ffmpeg -af loudnorm ...` (consistent levels across cases
  matters a lot for benchmarking comparability).
- **Do not** aggressively denoise before benchmarking — you'd be measuring your
  denoiser, not the ASR. Keep a raw and a cleaned version if you want to study it.

Record sample rate, channels, and duration in the manifest.

---

## 6. Speech: transcription + diarization + timing

> **Intent (2026-07-27): Whisper and pyannote run directly on this machine.**
> Transcription Suite was the old box's crutch and is *not* the target pipeline.
> `asr.py` therefore defaults to `--engine whisperx`, with `faster_whisper` as the
> pinned benchmark engine and `diarize.py` running pyannote directly (it emits the
> RTTM that DER scoring needs — §8b). The `suite` engine is retained only as a
> benchmark comparator against the transcripts the old machine already produced;
> nothing in the pipeline depends on it, and it can be deleted outright.
>
> Caveat worth stating plainly: the whisperx and faster-whisper paths are written
> from the sketches below and have **not been executed anywhere yet** — first run
> on the new box will need model downloads and HF auth, and should be treated as
> bring-up, not as a working stage.

Recommended default: **WhisperX**, which gives you word-level timestamps
(via wav2vec2 forced alignment) *and* speaker labels (via pyannote) in one pass —
exactly the "speech + timing + diarization" bundle you want for the dataset.

```python
# src/multidata/asr.py  (sketch, md-speech env)
import whisperx, gc, torch

device = "cpu"                     # or "mps"; CT2 pieces stay CPU regardless
audio_file = "data/audio/<case_id>/audio.wav"

# 1) transcribe
model = whisperx.load_model("large-v3", device, compute_type="int8")
audio = whisperx.load_audio(audio_file)
result = model.transcribe(audio, batch_size=8)

# 2) word-level alignment
align_model, meta = whisperx.load_align_model(result["language"], device)
result = whisperx.align(result["segments"], align_model, meta, audio, device)

# 3) diarization (needs HF token)
dia = whisperx.diarize.DiarizationPipeline(use_auth_token=True, device=device)
diarize_segments = dia(audio_file)                 # optionally min/max speakers
result = whisperx.assign_word_speakers(diarize_segments, result)

# result["segments"] now carry start/end/text/words[]/speaker
```

Persist a normalized JSON per case
(`data/transcripts/<case_id>/whisperx.json`) with, per word:
`{word, start, end, score, speaker}`. That JSON is the single source of truth
for §7 (ELAN) and §8 (benchmarking).

### 6a. Decoding hygiene / anti-hallucination

Two failure modes dominate on clinical audio, and **neither is fixed by a bigger
model** — `large-v3` is if anything *more* prone to the first. (There is no
"Whisper XL"; the largest is `large-v3`.)

**Hallucinated phrases on silence/non-speech** ("Thank you", "Thanks for
watching"). Whisper learned these from subtitle data and emits them over quiet or
non-speech audio. Fixes, in order of impact:
- **VAD gating** — don't send silence to Whisper at all. WhisperX applies VAD by
  default; with `faster-whisper` set `vad_filter=True`. Biggest single lever.
- `condition_on_previous_text=False` — stops hallucination loops / repeated text.
- Tighten decoding thresholds: `no_speech_threshold≈0.6`, keep
  `compression_ratio_threshold≈2.4` and `log_prob_threshold≈-1.0` active so junk
  segments are dropped; leave temperature fallback on so degenerate decodes retry.

**Domain substitutions** ("nurse" → "nerds"). This is vocabulary, not resolution:
- **`initial_prompt`** seeded with domain terms softly biases the decoder, e.g.
  `"A clinical conversation between a nurse, patient, and clinician."` Not a hard
  lock, but it measurably cuts exactly this class of error.
- Longer term: domain fine-tuning, or a post-ASR correction pass over a term list.

```python
# faster-whisper (the reproducible benchmark engine)
segments, info = model.transcribe(
    "audio.wav",
    vad_filter=True,
    condition_on_previous_text=False,
    no_speech_threshold=0.6,
    compression_ratio_threshold=2.4,
    log_prob_threshold=-1.0,
    initial_prompt="A clinical conversation between a nurse, patient, and clinician.",
)
```

> Treat `initial_prompt` as an **experimental variable in the benchmark** (§8),
> not a fixed constant — a prompt that helps one setting can bias another. Measure
> the "nurse/nerds"-class substitution rate with the prompt on vs. off.

**Bulk-processing model choice:** prefer **`large-v3-turbo`** (near-`large-v3`
accuracy, several× faster) as the default for 300–400 videos, and keep `large-v3`
as the accuracy anchor in the benchmark sweep.

**For benchmarking specifically** (§8), also run the *raw* engines with fixed
configs (no alignment glue) so you're measuring the model, not the wrapper:
`openai-whisper` and `faster-whisper` at a pinned model size, and `mlx-whisper`
for the Apple-Silicon path. Keep those code paths in `asr.py` behind a
`--engine` flag.

---

## 7. Build the ELAN (.eaf) file

`pympi-ling` writes ELAN files. One tier per speaker; annotations from the
WhisperX segments. ELAN then becomes your human-correction surface.

```python
# src/multidata/elan.py  (sketch)
from pympi.Elan import Eaf
import json, pathlib

def build_eaf(whisperx_json, media_path, out_path):
    data = json.load(open(whisperx_json))
    eaf = Eaf()
    eaf.add_linked_file(str(media_path), mimetype="video/mp4")
    speakers = sorted({seg.get("speaker","UNK") for seg in data["segments"]})
    for spk in speakers:
        eaf.add_tier(spk)
    for seg in data["segments"]:
        spk = seg.get("speaker","UNK")
        start_ms = int(seg["start"]*1000)
        end_ms   = int(seg["end"]*1000)
        eaf.add_annotation(spk, start_ms, end_ms, seg["text"].strip())
    eaf.to_file(str(out_path))
```

> **As built, `elan.py` is word-level**, not the utterance-level sketch above —
> it is the builder promoted from `transcription_suite.ipynb` (one annotation per
> word, one tier per speaker, tiers created on demand, bare-integer speaker ids
> normalized to `SPEAKER_00`). It reads either root `words` or `segments[].words`,
> so it accepts any of the three ASR engines' output.

Design choices to decide early (they affect every downstream .eaf):
- **Tier granularity:** utterance-level (segments) vs word-level tiers. Start
  utterance-level; add a word tier only if the analysis needs it.
- **Link the video, not just audio**, so annotators see gesture + speech together.
- Keep the generated `.eaf` as the *machine draft*; save human-corrected versions
  under a distinct name so you never overwrite hand-labels with a re-run.

---

## 8. ASR benchmarking wing

This is a first-class part of the project, not an afterthought. Purpose: measure
how well ASR/diarization actually work on *your* clinical audio, and compare
engines/configs objectively.

### 8a. What you need
- **Gold references.** Human-verified transcripts for a held-out subset (start
  with 5–10 cases). Store as `benchmarks/references/<case_id>.txt`
  (or CTM/RTTM for timing/diarization). This is the expensive, essential part —
  the benchmark is only as good as these.
- **Frozen audio.** Reference audio must never change; hash it.

### 8b. Metrics
| Question | Metric | Tool |
|---|---|---|
| Transcription accuracy | WER, CER | `jiwer` |
| Speaker attribution | DER, JER | `pyannote.metrics` |
| Word timing accuracy | mean/median boundary error | custom vs gold CTM |

### 8c. Sweep design
Vary **one axis at a time**, everything else pinned:
- model size: `tiny → base → small → medium → large-v3`
- engine: `openai-whisper` vs `faster-whisper` vs `mlx-whisper`
- VAD on/off, `min/max speakers` hints, language forced vs auto
- audio: raw vs loudness-normalized vs denoised

```python
# benchmarks/run_benchmark.py  (sketch)
import jiwer, json, hashlib, subprocess, datetime, pandas as pd

def wer(ref, hyp):
    t = jiwer.Compose([jiwer.ToLowerCase(), jiwer.RemovePunctuation(),
                       jiwer.RemoveMultipleSpaces(), jiwer.Strip()])
    return jiwer.wer(t(ref), t(hyp))

def record(row: dict):
    row["timestamp"] = datetime.datetime.now().isoformat()
    row["git_commit"] = subprocess.getoutput("git rev-parse --short HEAD")
    pd.DataFrame([row]).to_csv("benchmarks/results/runs.csv",
                               mode="a", header=False, index=False)
```

### 8d. Provenance (log with EVERY run)
engine, model name+version, params (beam/temperature/VAD), audio hash, git
commit, wall-clock time, machine. Append-only `benchmarks/results/runs.csv`. A
benchmark you can't attribute to an exact config is noise.

> **Clinical-domain reality:** off-the-shelf Whisper struggles with medical
> terminology, overlapping speech, and quiet/multi-party rooms. Expect higher WER
> than the marketing numbers. That gap *is* a finding — it's arguably the whole
> point of the benchmarking wing. Report it, don't hide it.

---

## 9. Pose estimation (rtmlib) — `md-pose` env

Reuse your existing rtmlib cells, promoted from notebook to `src/multidata/pose.py`
and pointed at the new layout. Per camera → `data/pose/<case_id>/<camera>.pkl`
in the `(M, T, V, C)` / `(M, T, V)` format you already produce.

Apple-Silicon speed options (in rough order of effort/payoff):
- **CoreML execution provider** for onnxruntime can accelerate inference on the
  Neural Engine/GPU: `onnxruntime` with `providers=["CoreMLExecutionProvider","CPUExecutionProvider"]`.
  Verify rtmlib passes providers through; if not, this is a small patch. Benchmark
  it — CoreML EP helps some ops, not all.
- **Down-sample frame rate** (e.g. process every 2nd–3rd frame) if your analyses
  tolerate it. Biggest single lever on total runtime.
- **Lighter model mode** (`mode="lightweight"`) if accuracy allows.

> Keep the tracker-off, detection-order-slots behavior from the notebook for now;
> stable person identity / cross-camera correspondence is still deferred work, not
> a pipeline blocker.

---

## 10. The manifest — the spine of the whole thing

A `sqlite3` database (`manifest.sqlite`), not a flat CSV, because the data
itself is two different shapes — this is what makes 300–400 videos tractable
instead of chaos.

**`cases`** — one row per recorded encounter, as exported by the source
system. `case_id` is the source system's own identifier — the sheet title in
the source system's Excel video-manifest export (see `data/cse2b_videos.xlsx`
for an example). It is **not** derivable from the raw video filename (§2) —
there's no case_id in the filename at all, only a timestamp that superficially
resembles one.

```
case_id, event_name, case_name, group_name, room_number,
learner_name, sp_name, recording_start_time, consent_ref
```

**`videos`** — one row per physical **camera-file**, usually two per case
(§2's N-camera convention). This is the table every processing stage actually
reads and writes.

```
case_id, camera, recording_id, video_date, video_time, filepath, sha256,
duration_s, fps, width, height, has_audio, audio_path, ingest_ok,
audio_status, asr_status, diarize_status, elan_status, pose_status,
asr_model, asr_commit, notes
```

`camera`, `recording_id`, `video_date`, `video_time` come straight from the §2
filename spec — `ingest.parse_filename()` / `ingest.summarize()` extract them,
they aren't assigned separately. `case_id` does **not** come from the
filename; it's resolved by `casematch.resolve()` (§2/§4) by matching
`video_date`+`video_time` (truncated to the minute) and the camera's room
(`data/room_camera_map.csv`) against `cases.recording_start_time` /
`cases.room_number`.

(Built in `src/multidata/models.py` — `Case`, `Camera`, `Video` dataclasses —
and persisted via `src/multidata/manifest.py`. `audio_status` was added so the
§5 stage is resumable like the rest.)

`*_status` ∈ `{pending, done, failed, skipped}`. Every stage reads the manifest,
processes only `pending` video rows, writes outputs, and updates status. That
gives you **idempotent, resumable batch runs** — kill it overnight, restart, it
picks up where it left off.

**`video_case_matches`** — the persistent cache behind `casematch.resolve()`
(§2/§4): one row per raw filename, `status` ∈
`{matched, ambiguous, no_match, no_room}`, `case_id` set only when `matched`.
Avoids rescanning every case on every ingest run, and gives you one place
(`manifest.unresolved_matches()`) to see every file still needing a manual
look.

`manifest.import_video_manifest_excel(xlsx_path)` unpacks a source-system
video-manifest workbook — one sheet per encounter, sheet title = `case_id` —
and inserts any `cases` rows not already in the database. It's additive only;
re-running it against the same (or a refreshed) workbook is a no-op for cases
already present. It needs `openpyxl`, so it's imported lazily inside that one
function — `manifest.py` otherwise stays stdlib-only (`sqlite3`) so it still
imports in both md-speech and md-pose. `videos` rows are *not* populated by the
Excel import (the source export has no per-camera file info); they're added by
the ingest stage (§4) once the actual camera files exist, via
`manifest.add_video(case_id=casematch.resolve(path), **ingest.summarize(path))`
— `summarize()` parses `camera`/`recording_id`/`video_date`/`video_time` from
the filename (§2) and folds them in alongside the ffprobe/hash facts,
`casematch.resolve()` supplies `case_id` separately, and `add_video` refuses
to add a video for a case that hasn't been imported/added yet.

```python
# scripts/run_stage.py  (sketch)
#   python scripts/run_stage.py audio
#   python scripts/run_stage.py asr --only 132704
# reads manifest.sqlite, dispatches per row to the right src/multidata function,
# updates status, appends errors to a log. Keep it boring and restartable.
```

---

## 11. Scaling from 1 → few → 300–400 videos (my other thoughts)

You asked for thinking on the process at scale. The tooling above is built for it,
but a few things dominate whether this works:

### Compute is the real constraint, and **pose is the bottleneck**
Your own note: ~10 min to process ~60 s of Body pose on a laptop — that's ~10×
slower than real time. Napkin math for pose alone:

> 400 videos × 20 min each × 10× realtime ≈ **1,300+ hours** of compute.

That does **not** finish on one Mac mini in any reasonable window. Implications:
- **Pose must get faster or move off-box** before full scale: CoreML EP, frame
  down-sampling, and/or offloading pose to a GPU box or cloud batch. Prototype and
  measure the mini's real per-minute pose cost on 3–5 cases *first*, then
  extrapolate before you commit to a plan.
- ASR (mlx-whisper) and diarization are far cheaper (often faster-than-realtime);
  they'll scale on the mini fine.
- Design for **overnight, unattended, resumable** batch runs (that's what §10
  buys you). Process **stage-by-stage across all videos**, not video-by-video —
  easier to monitor, checkpoint, and parallelize per stage.

### Data volume & governance
- 300–400 multi-camera videos is **terabytes** of raw. The Mac mini is almost
  certainly not enough, and a loaned device is the wrong long-term home for PHI.
  Plan storage (encrypted external / institutional secure storage) and a
  **backup** strategy early. Raw is irreplaceable; derived is regenerable.
- **IRB / consent tracking is a pipeline field, not paperwork off to the side.**
  Put `consent_ref` in the manifest and *refuse to process* rows without it.
- Consider a **de-identification** stage (face blurring for shareable clips; PHI
  scrubbing in transcripts) as a first-class, logged step if data ever leaves the
  secure environment.

### Reproducibility & drift
- **Pin versions** in the env files and record model versions per output. Whisper,
  pyannote, and rtmlib all change behavior across releases — an unpinned upgrade
  silently invalidates comparisons across your 400 videos.
- Re-running a stage should be deterministic given the same inputs + config, or
  the benchmarking wing is meaningless.

### Human-in-the-loop, sampled not exhaustive
- You won't hand-correct 400 videos. **Sample** for QC: correct a stratified
  subset in ELAN, measure error rates there (§8), and report dataset quality with
  confidence intervals rather than pretending it's all gold.
- Build the correction loop (machine draft `.eaf` → human edit → store separately)
  early, on the first few videos, so it's ready when volume arrives.

### Suggested phasing
1. **1 video, end to end**, all stages, manually verified. Prove the plumbing.
2. **~5 videos**, batch-driven via the manifest + `run_stage.py`. Prove
   resumability and measure real per-stage timings on the mini.
3. **Extrapolate compute/storage** from (2). *Then* decide: does pose stay local,
   or move to GPU/cloud? Does raw storage stay on the mini?
4. **Scale up** only once (1)–(3) hold, adding the scripted ingest (§4b) and
   de-id stage as needed.

---

## Appendix — quick command reference

```bash
# activate the right env for the stage
conda activate md-speech      # audio, asr, diarize, elan, benchmarks
conda activate md-pose        # pose

# extract audio
ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -sample_fmt s16 out.wav

# run a stage across all pending rows
python scripts/run_stage.py audio
python scripts/run_stage.py asr
python scripts/run_stage.py pose

# one benchmark run
python benchmarks/run_benchmark.py --engine faster-whisper --model large-v3
```

*This is a living document. Update it as the mini reveals real timings and the
source-system ingest gets figured out.*
