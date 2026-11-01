# Data formats

Every file the pipeline writes: where it is, what's in it, its units and
clock, and how to read it. How each stage makes its output is in
[pipeline.md](pipeline.md); this page is for using the outputs.

---

## Conventions

- **Keys.** Everything is keyed by `(case_id, camera)` and lives at
  `data/<kind>/<case_id>/<camera>.<ext>`. `case_id` comes from the source
  system's export; `camera` is the camera number from the raw file name.
- **Clocks.** Times are in **seconds**.
  - A video's **file clock** starts at 0 at the start of that file.
  - The **reference clock** is the file clock of the case's **audio camera**
    (`cases.audio_camera`). Transcripts, diarization, prosody and ELAN are all
    made from that camera's WAV, so their times are already on the reference
    clock. To put another camera's video or pose on it, add that camera's
    `videos.sync_offset_s` (pipeline §9b).
  - Assumed and not yet verified: that a file's audio and video share time 0
    (the calibration session's clap shots will check it).
- **Missing values.** NaN in arrays, empty cells in CSV, `NULL` in the
  manifest, `None`/`null` in JSON. Never 0 for "unknown".
- **Speaker labels** (`SPEAKER_00`, ...) are per case: the same label in two
  cases is not the same person.
- **Names.** Real names appear in `cases` and can appear in transcripts
  (spoken). Don't put either in anything shared.
- **Reading in R.** CSV, RTTM, Praat and ELAN files read directly
  (`read.csv`, `rPraat`, ...). `.npz` files are NumPy archives: read them with
  `reticulate::import("numpy")$load(path)`, or export to CSV from Python.

---

## Audio: `data/audio/<case>/<camera>.wav`

16 kHz, mono, 16-bit PCM WAV, from the audio camera only. Standard: opens in
anything (Praat, Audacity, `soundfile`, `tuneR`).

## Transcripts: `data/transcripts/<case>/<camera>_<engine>_<model>.json`

JSON, one file per engine and model (e.g. `20_faster_whisper_medium.json`).
Whisper-style, not a formal standard:

```
{ "language": "en", "engine": "faster_whisper",
  "segments": [ { "start": 2.14, "end": 3.26, "text": "...", "speaker": "SPEAKER_02",
                  "words": [ { "word": "Fifteen", "start": 2.14, "end": 2.6,
                               "score": 0.91, "speaker": "SPEAKER_02" }, ... ] }, ... ] }
```

`start`/`end` in seconds, reference clock; `score` is the engine's word
confidence (0-1); `speaker` from diarization. A `.txt` beside it is a
readable rendering (`[hh:mm:ss] SPEAKER: text`), not for parsing. Read with
`json.load`; `multidata.elan.words_of(data)` flattens the words.

## Diarization: `data/diarization/<case>/<camera>.rttm`

**NIST RTTM**, one line per speaker turn:
`SPEAKER <file> 1 <start_s> <duration_s> <NA> <NA> <speaker> <NA> <NA>`.
Reference clock. Read with pyannote (`pyannote.database.util.load_rttm`),
`multidata.prosody.read_rttm` (no dependencies), or as whitespace-separated
text.

## ELAN draft: `data/elan/<case>/<camera>.eaf`

**ELAN XML** (`.eaf`), linked to the audio camera's video and WAV. Tiers:

| Tier | Contents |
|---|---|
| `SPEAKER_00`, `SPEAKER_01`, ... | one annotation per word, from the transcript |
| `LEARNER`, `PATIENT`, `PRECEPTOR`, `ANNOUNCEMENT`, `OUTSIDE_ROOM` | empty: role tiers named as in the gold template, for side-by-side reading |
| `PITCH_HZ` | one annotation per stretch of continuous voicing, value = median pitch in Hz (from prosody; independent of the transcript) |

This is the machine draft, not gold. Open in ELAN; read with `pympi`
(`pympi.Elan.Eaf(path)`).

## Gold references: `benchmarks/references/<case>.gold.*`

In git. Made by `scripts/eaf_to_gold.py` from the `.eaf` in
`data/gold/<case>/`.

- `.gold.txt`: the adjudicated transcript of the encounter as one line of
  text, names replaced by tags like `[LEARNER_NAME]`
  ([transcription_standards.md](transcription_standards.md)).
- `.gold.rttm`: RTTM of the annotated turns, with role names as speakers
  (`LEARNER`, `PATIENT`, `ANNOUNCEMENT`, ...).
- `.gold.json`: how pass 1 was made. `pass1_source` is `blind` or `seeded`;
  seeded gold adds `seed_engine`, `seed_model`, `seed_transcript`,
  `seed_sha256` (of the exact transcript) and `roles` (the speaker -> role
  map). Also `standards_version`, `gold_at`, `span`. A reference without this
  file predates it and is blind. Read with
  `multidata.seeding.read_gold_meta(case, "benchmarks/references")`.
  **Never score the seed engine against seeded gold**, and report seeded and
  blind results separately (standards §9).

The manifest's `gold` table holds the same provenance per gold row
(`pass1_source`, `seed_engine`, `seed_model`, `seed_sha256`).

## Prosody: `data/prosody/<case>/<camera>.*`

Made from the audio camera's WAV and the diarization only, never the
transcript, so switching ASR doesn't change it (except the speaker summary,
if the diarization changes).

**`<camera>.npz`**: frame features, one row per 10 ms frame (NumPy archive):

| Array | Units | Notes |
|---|---|---|
| `time_s` | s | frame centre, reference clock |
| `f0_hz` | Hz | pitch (fundamental frequency); NaN where unvoiced. Range 75-600 Hz; values near either end are often Praat errors on noise |
| `intensity_db` | dB | loudness, **uncalibrated**: compare within a recording, not across rooms |
| `hnr_db` | dB | harmonics-to-noise ratio (voice quality: higher = clearer); NaN in silence |
| `f1_hz` .. `f4_hz` | Hz | formants 1-4 (vowel quality); NaN where Praat finds none |
| `params` | JSON string | Praat and parselmouth versions, time step, pitch range, duration |

Read with `multidata.prosody.load(path)` (a dict of arrays, `params` parsed)
or `numpy.load(path)`.

**Praat files**, for opening with the WAV in Praat (Praat text format):

| File | Praat object | Contents |
|---|---|---|
| `<camera>.PitchTier` | PitchTier | pitch (Hz) at every voiced frame |
| `<camera>.IntensityTier` | IntensityTier | loudness (dB) at every frame |
| `<camera>.TextGrid` | TextGrid | one interval tier per diarized speaker: the speaker's label over their turns, empty between |

In Praat: *Open > Read from file* for the WAV and these, select the Sound with
the TextGrid and choose *View & Edit*. In Python, `parselmouth.read(path)`.

**`<camera>_speakers.csv`**, also in the manifest table `speaker_prosody`:
one row per diarized speaker. Frames where two speakers overlap are left out
of every speaker's figures. Pitch figures are empty when a speaker has fewer
than 10 voiced frames.

| Column | Units | Meaning |
|---|---|---|
| `speaker` | | diarization label |
| `turns` | count | diarized turns |
| `speaking_s`, `overlap_s`, `voiced_s` | s | time in turns, time overlapping another speaker, voiced time outside overlaps |
| `median_f0_hz`, `f0_p10_hz`, `f0_p90_hz` | Hz | pitch median and 10th/90th percentiles |
| `f0_range_st` | semitones | 90th vs 10th percentile: pitch range, comparable across voices |
| `f0_sd_st` | semitones | spread of pitch around the median |
| `mean_intensity_db`, `median_hnr_db` | dB | while voiced |
| `pitch_bucket` | | `lower` / `ambiguous` / `higher` vocal register: for sampling annotation cases only, never a statement about a person |

`manifest.export_speaker_prosody(path)` writes every case's rows to one CSV.

## Camera sync

- **Manifest** `videos.sync_offset_s`: seconds to add to this file's clock to
  get the reference clock (0 for the audio camera; NULL if sync failed).
  `videos.sync_agreement`: share of audio windows agreeing (1.0 = clean).
- **`data/sync/<case>/<camera>.npz`**: `frame_time_s`, the presentation time
  of every video frame in the file, file clock, in display order.
  `multidata.sync.load_frame_times(path)`.

## Pose: `data/pose/<case>/<camera>.npz`

NumPy archive. Keypoint set **HALPE_26** (standard; names and order in
`multidata.pose.KEYPOINTS`: COCO's 17 body points, then head top, neck,
mid-hip, six foot points). Pixel coordinates in the original frame (x right,
y down).

| Array | Shape | Units / notes |
|---|---|---|
| `keypoint` | `(M, T, 26, 2)` | pixels (float16 on disk: ≤0.5 px rounding inside a 1920-wide frame) |
| `keypoint_score` | `(M, T, 26)` | 0-1 confidence; use > 0.3 as "confident" |
| `bbox` | `(M, T, 4)` | detector box `x0, y0, x1, y1`, pixels |
| `frame_ms` | `(T,)` | file-clock time of each frame pose read, milliseconds |
| metadata | scalars | `frame_dir`, `total_frames`, `img_shape` (height, width), `keypoint_set`, `models`, `mode`, `detect_every`, `max_persons`, `rtmlib_version` |

`M` = 8 detection slots per frame; unused slots are all zeros. **A slot is not
a person**: slot 0 in one frame and the next can be different people (no
tracking). `T` = frames OpenCV read, which can be fewer than the file holds,
so pair frames by time, never by index:
`multidata.sync.pose_reference_times(entry["frame_ms"], sync_offset_s)` gives
each frame's reference-clock time. Read with `multidata.pose.load(path)`
(float32 arrays).

## Camera views

- **`data/views/<case>/<camera>.json`**: `video`, `camera`, `sync_offset_s`,
  `keyframes`, `keyframe_period_s`, `params`, and `stretches`, a list of
  periods the camera held still:

  | Field | Meaning |
  |---|---|
  | `t0`, `t1`, `dur_s` | start, end, length; file clock, s |
  | `ref_t0`, `ref_t1` | same on the reference clock (null without a sync offset) |
  | `view` | view id (`v1`, `v2`, ... per camera); null for stretches under 30 s |
  | `match` | `[dx, dy, zoom, rotation]` of this stretch against its library view |
  | `move_from_prev` | the same, against the previous stretch (how far the camera moved) |

  Shifts are in pixels of a 320-wide frame; rotation in degrees.
- **`data/views/library/<camera>/v<n>.npz` / `.png`**: 5 sample frames of the
  view (320x180 grey); the `.png` is the same as a strip.
- **Manifest** `camera_views` (one row per view: `label` = preset name,
  `label_source` = `provisional` / `confirmed`, `notes`) and `video_views`
  (seconds and stretches per video per view). `manifest.view_summary()` joins
  them and marks recurring views (presets).

## The manifest: `manifest.sqlite`

SQLite. Tables and what they hold: [pipeline.md](pipeline.md) §10. Read with
`sqlite3` (any language). `stage_runs.params` and `stage_runs.details` are
JSON strings. Treat it as read-only unless you're a pipeline stage; take a
copy with `sqlite3 manifest.sqlite ".backup copy.sqlite"`.

## Benchmark results: `benchmarks/results/runs.csv`

In git, append-only, one row per scored run: `timestamp, case_id, engine,
model, audio_sha256, wer, cer, wall_s, source, git_commit, machine, notes,
gold_pass1` (`source`: `live` = engine ran, `cache_hit` / `cached` = it
didn't; `gold_pass1`: `blind` or `seeded`, empty on rows from before the
column existed, which are blind). Detail
per run in `benchmarks/runs/<run_id>/` (gitignored).

---

## Not yet

- No file carries a format version; when a format changes, the stage's
  module docstring and this page say so, and old files should be regenerated
  rather than mixed.
- The transcript JSON doesn't record the model version or settings (the
  manifest's `videos.asr_model` and `stage_runs.params` do).
