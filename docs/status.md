# Project status

**Last updated: 2026-11-16.** Update this file whenever a working session
changes what's true; delete what stops being true rather than letting it
rot. History lives in git and in the decision log at the end.

---

## In one paragraph

The pipeline takes clinical-encounter video (simulated encounters in our sim
center, two PTZ cameras per exam room) to word-level transcripts with
speakers, ELAN files, prosody, camera sync, camera-view segmentation and 2D
body pose, all on the pilot's processing host (`tofino`). 57 recordings (114 videos, ~75 h) are ingested,
all in the small exam rooms. Two threads are active: **validating
transcription** (gold annotation and an ASR bake-off) and **moving pose to
3D** (waiting on a room calibration session; see [pose_3d.md](pose_3d.md)).

---

## What's built, and how real it is

| Area | State |
|---|---|
| Ingest (`ingest_raw.py`, walkthrough notebook) | Working on real files; 2026-11-01 run added 4 cameras |
| audio, asr, diarize, elan | Run on every recording. Re-run 2026-11-02 with `whisperx_disfluent large-v3` (keeps disfluencies) on pyannote community-1 diarization |
| prosody | Stage since 2026-11-01; run on all 57 recordings (246 speakers) |
| sync | Every video; 5 rows fail (room 11's camera 31, 261041/41, a 2-min clip) and get no offset |
| views + preset names | Every video; 65 recurring views provisionally named, awaiting confirmation |
| pose (HALPE_26) | Full re-run 2026-10-30 to 11-01; the 4 newly added cameras follow |
| 3D | Not started: needs calibration ([pose_3d.md](pose_3d.md)) |
| ASR benchmarking | `transcribe.py` (named settings) then `run_benchmark.py` (WER at L0/L1 with names redacted and numbers canonicalized; cpWER, DER, JER); every engine scored on five gold references (2026-11-16) |
| Cloud ASR adapters | AssemblyAI, ElevenLabs, Google Chirp 3 and Gemini 3.5 Transcribe behind one interface with local Whisper (`multidata.transcribers`); all run live on gold 2026-11-16 |
| Cost tracking | Every stage run recorded in `stage_runs`; [cost_estimate.md](cost_estimate.md) |

- **Open (noted 2026-11-03): audio clock on room 11's camera 16.** That
  camera dropped sound during its recording gaps without leaving silence,
  so the pipeline's wav (`data/audio/<recording>/16.wav`) is shorter than the
  video and runs ahead of it by a growing amount. Everything timed on the
  wav is off against video and pose by the same amount: ASR and
  diarization drafts, prosody, ELAN kits, human pass-1s and any gold
  export. Affected (3 of 72 camera recordings): **261456** (14.8 s by the
  end, mostly accrued in the first 13 min; has two human pass-1s),
  261505 (9.6 s), 261485 (3.6 s). Measured by matching the wav against the
  video's soundtrack decoded with gaps filled (`aresample=async=1`). The
  demo branch corrects its own copy (`audio_clock` in
  `demo/build_case.py`); the pipeline, kits and transcripts are unchanged.
  Fix options to weigh later: extract audio with gaps filled (changes every
  time in those recordings, so existing annotations would need re-timing), or
  keep wav time and store a wav-to-video time map per recording.

---

## Transcription validation

**Gold:** five references in `benchmarks/references/`, standards 1.2:
**261498** (pass 1 Kelli, pass 2 Kate), **261505** (pass 1 Xiaomei, pass 2
Kate), and since 2026-11-13 **252848**, **261456** and **261473**
(withdrawn to pass 1 on 2026-10-28, then re-reviewed by Kate; the last two
without a formal pass-2 assignment). Their older rows in `runs.csv` were
scored the old way and are history only.

Scores against current gold (2026-11-16, scoring v5: numbers
canonicalized, speakers scored; all five gold recordings, ~14,400 words, pooled by gold word
count; `runs.csv` has each recording):

| Engine | L0 WER | L1 WER | cpWER | DER | Per recording L0 (252848, 261456, 261473, 261498, 261505) |
|---|---|---|---|---|---|
| `assemblyai universal-3-5-pro` | 0.162 | 0.165 | 0.171 | 0.282 | 0.16 0.17 0.19 0.16 0.16 |
| same, `domain=medical-v1` | 0.162 | 0.165 | 0.169 | 0.283 | 0.16 0.17 0.19 0.16 0.16 |
| `gemini gemini-3.5-transcribe` | 0.169 | 0.171 | 0.259 | 0.262 | 0.15 0.18 0.24 0.19 0.16 |
| `elevenlabs scribe_v3` | 0.187 | 0.188 | 0.201 | 0.305 | 0.19 0.19 0.23 0.20 0.16 |
| `elevenlabs scribe_v2_medical` (BAA model) | 0.194 | 0.194 | 0.283 | 0.336 | 0.20 0.19 0.26 0.17 0.17 |
| `google chirp_3` | 0.207 | 0.209 | 0.268 | 0.247 | 0.20 0.18 0.30 0.33 0.18 |
| `faster_whisper medium` | 0.243 | 0.211 | 0.343 | 0.225 | 0.26 0.20 0.35 0.28 0.22 |
| `whisperx large-v3` | 0.267 | 0.239 | 0.355 | 0.362 | 0.26 0.29 0.33 0.25 0.25 |
| `whisperx_disfluent large-v3` | 0.265 | 0.264 | 0.367 | 0.388 | 0.21 0.32 0.37 0.20 0.29 |

cpWER is WER with words matched to the right person; DER (pooled by gold
speech time) is built from each engine's own word timings, so it also
counts speech an engine didn't transcribe (rules: asr_evaluation_methods.md
§4.3). AssemblyAI is best on every recording; Medical Mode changes nothing (not
worth +$0.15/h). ElevenLabs' BAA model costs ~0.7 points against
`scribe_v3`. Google is uneven (0.30–0.33 on the two short chest-pain
recordings; not yet looked at). Still five recordings and no noise floor.
Variant runs live in `data/transcripts/<recording>/variants/<setting>/`; the
ledger names the setting and the transcript file. Next steps: the list below.
**Pending gold:** 261466 (2026 long station, abdominal pain; the largest
scenario had none) and 259392 (the only CSE 2A recording: GI history and presentation)
went to Kate as seeded pass 1s on 2026-11-16. The procedure is
[asr_evaluation_methods.md](asr_evaluation_methods.md); its §5 lists what
the scores don't yet account for.

**Annotation** runs through Box: kits built on the processing host
(`annotation/make_kit.py`), shared per annotator, returned there; who has
which pass is in the manifest's `assignments` table (`annotation/assignments.py list`) ([../annotation/README.md](../annotation/README.md)).
Annotators are scarce, so pass 1 can now start from a machine draft (route B,
standards §9): seeded gold adds recordings, but engine rankings rest on blind gold.

### Cloud ASR: where things stand (2026-11-16, branch `asr-benchmarking`)

IRB cleared sending audio to vendors for inference (2026-09-10); all 57
recordings have `cloud_release=1` (basis "simulated patient"). Vendor facts
are in `benchmarks/configs/providers.yaml`; the design is
[asr_provider_spec.md](asr_provider_spec.md); results so far are in
[asr_benchmark_findings_2026-11.md](asr_benchmark_findings_2026-11.md).

**Built** (merged to `main` in #16): one interface
(`src/multidata/transcribers/`) for local Whisper, AssemblyAI, ElevenLabs,
Google Chirp 3 and Gemini 3.5 Transcribe (Vertex AI only). Cloud engines
refuse a recording without `cloud_release`. Long audio is cut where the
diarize stage's turns change hands; vendor speaker labels are linked
through the overlaps, else through the diarize turns. Benchmarking is
`benchmarks/transcribe.py` (named settings in `configs/settings.yaml`;
skips what exists, saves each vendor response before normalizing) then
`benchmarks/run_benchmark.py` (scores WER, cpWER, DER, JER).
`scripts/smoke_cloud.py` checks a vendor on a name-free 60 s clip (252848,
344.5–404.5 s).

**Standing facts:**
- ElevenLabs only works with `enable_logging: true` (zero-retention is
  enterprise-only); Kate approved logging for the gold recordings only.
- Vertex rate-limits Gemini (429); the adapter backs off and retries.
- On tofino, `conda run -n md-speech` runs md-utility's Python (PATH order);
  call `~/miniconda3/envs/md-speech/bin/python` directly. pympi-ling, jiwer
  and the Google clients also sit in md-utility by mistake.
- `benchmarks/runs/` and `benchmarks/cache/` are no longer written and can
  be deleted from disk.

**Pick up here, in order:**

1. **Kate: confirm terms.** AssemblyAI's BAA (the lead candidate), and
   Gemini's BAA coverage and price (neither is on its model page), before
   any recording beyond gold goes to either.
2. **Kate: listen to 252848 at 44:10–45:35.** Gold has 3 words where the
   diarize stage and AssemblyAI (64 words) hear speech; probably a gap in
   gold, which costs every engine in DER and insertions.
3. **Back-channels.** AssemblyAI keeps 9 of gold's 187 "mm-hmm"/"uh-huh"
   (Gemini 55, ElevenLabs 114). Listen to a sample, and try one AssemblyAI
   `prompt` setting asking it to keep them (~$0.50 over gold).
4. **Free combinations from saved responses.** AssemblyAI's or Gemini's
   words with the diarize stage's speakers (`speakers: pipeline`), which
   has the best DER. A new setting needs its `responses_dir` pointed at the
   original setting's saved responses, or `transcribe.py` will call the
   vendor again.
5. **Gemini's speaker linking.** Its cpWER (0.259) suffers from 14-minute
   pieces; look at which links fail on 261505 (cpWER 0.43).
6. **Noise floor:** a second annotator on one or two blind excerpts; this
   also settles how many of ElevenLabs'/Google's extra words are real.
7. **More gold:** seeded pass 1s of 261466 and 259392 are with Kate;
   261041 (seizure) is at pass 2.
8. **At scale:** `run_stage.py` running the winning setting over all
   recordings, once 1 and 3 are settled. Glossary runs wait for richer gold.

Decided along the way: capture verbatim once and derive a clean
(disfluency-free) view from it, rather than a second vendor run; vendors'
"smart"/clean modes drop speaker labels and timestamps.

---

## Governance facts

- **Identifiable data.** The patient is an actor, but learners and
  preceptors are real and say their names; transcripts carry them, and the
  `recordings` table holds real names. Never print names in shared output.
- **Storage:** the corpus may live in the institution's enterprise Google
  instance under BAA. A BAA covers a named list of services, not "Google"
  in general.
- **Vendor inference:** IRB-cleared (2026-09-10). Each recording sent needs
  `cloud_release=1` and a basis; all 57 have it (2026-11-16).
- **Pose:** HALPE_26 keeps 5 face points only, partly so the dataset can't
  identify faces. Demo clips blur faces by default (best effort: check before
  sharing).
- **Assessment use** (judging learners) would likely need its own IRB and
  consent coverage, separate from research use.
- **Open (noted 2026-11-03): names in public git history.** The first
  commit (`b3b248b`, 2026-07-29) included `manifest.sqlite` with full
  learner and SP names for 755 recordings. The next commit deleted it, but it
  stays in history, and the repo was public until 2026-11-03 (no forks).
  Still to decide: purge history (`git filter-repo`, force-push every
  branch, ask GitHub to purge cached views) and whether to tell the IRB or
  privacy office.
- **Closed 2026-11-11: preceptor redaction gap.** `preceptor_name` now comes
  from LearningSpace (the faculty observer) for 56 of 57 recordings, and
  `eaf_to_gold.py` warns when a speaker tier has speech but no name. Both gold
  files were re-exported. A short form of a name (261498's preceptor
  introducing themselves) still slips past matching, so reading each
  `.gold.txt` for names before committing stays a required step; that one is
  masked by hand. Older versions of `261498.gold.txt` in git history still
  carry it (same decision as the history item above).
- **Open (noted 2026-11-16): the committed gold text carries hand
  corrections the redactor can't reproduce.** Re-exporting 261505, 261456 or
  252848 from its `.eaf` brings back surnames and short forms of names that
  the committed files mask, and 261456's export masks the ordinary word
  "roads" as a preceptor name. So never regenerate a `.gold.txt` without
  diffing it against the committed one; edit gold text in place (as the
  number fixes were). The scorer runs the same redactor on hypotheses, so
  engines that hear those names take a small insertion penalty.

---

## Deliberately not built

So a fresh read doesn't mistake these for forgotten:

- A glossary -> Whisper prompt renderer (Phase 3.4): the loader exists.
- Cutting an `.eaf` down to an excerpt at export (Phase 5.2): scoring a span
  works; annotators only segment the excerpt anyway.
- Scripted download from the recording system: not until manual ingest has
  been routine for a while.
- Trimming videos to the encounter (decided 2026-11-01: chopping videos makes
  things confusing; pose runs on the whole file).

---

## Decision log

Newest first. One line each; the reasoning is in the linked doc.

| Date | Decision |
|---|---|
| 2026-11-16 | Chunk speaker labels the overlap can't link are linked through the diarize stage's turns (whole-recording labels), never merged within a piece; the chunk planner ignores sub-0.3 s diarize turns; an overlap comes whole from a piece that heard twice as many words there. |
| 2026-11-16 | One transcriber interface for local and cloud engines; transcript runs indexed in the manifest (`transcripts`), files stay authoritative. Long audio chunked at turn-rich overlaps with vendor speakers linked through them; silences sent, not skipped ([asr_provider_spec.md](asr_provider_spec.md) §5, plan 7.4). |
| 2026-11-11 | Our words follow LearningSpace's: what we called a *case* is a **recording** (`recording_id`, same numbers); *case* now means the scenario (`case_title`); `event_name` is `activity_title`; a video's `v309407` number is `file_number`. The manifest renames itself on first open. Recordings are linked to the LearningSpace Data API (`scripts/ls_backfill.py`): GUIDs, exact start, file links, preceptor (the faculty observer) and grade. The manifest was trimmed to the 57 recordings with video. |
| 2026-11-11 | Gold text includes `ANNOUNCEMENT` and `OUTSIDE_ROOM` (standards 1.2): the question is whether engines hear what humans hear. Scorer redacts names on both sides and uses the L0/L1 profiles. Pass-2 kits and gold import scripted (`make_pass2_kit.py`, `eaf_to_gold.py --from-kit`). |
| 2026-11-01 | Draft-seeded gold (standards 1.1, route B) as a fallback when annotators are scarce: pass 1 corrects a fixed `whisperx_disfluent`/`large-v3` transcript, provenance recorded, seed engine never scored against its own seeded gold. |
| 2026-11-01 | Prosody is a pipeline stage (was notebook-only). Transcription Suite engine removed. Operators' preset names from Drew; recurring views labelled provisionally. Large rooms allowed again for transcription/2D; the 3D pilot stays small-room. No video trimming before pose. |
| 2026-10-30 | Pose restarted for 3D: HALPE_26, 8 slots, WholeBody files deleted; large-room data deleted; own 3D module with Pose2Sim as reference ([pose_3d.md](pose_3d.md)); every stage's cost recorded in `stage_runs`. |
| 2026-10-29 | Camera sync from the shared audio feed + per-frame timestamps; offsets never guessed. |
| 2026-10-26 | Everything on tofino, one manifest writer, nightly backup to `/wddata`; three drifted manifests merged (most advanced status wins). Pose output float16 `.npz`. |
| 2026-09-23 | Cloud annotation desktop abandoned; annotation through Box (below). |
| 2026-09-10 | IRB cleared vendor inference for ASR. |
| 2026-08-26 | Gold is annotated blind, from scratch, never by correcting a machine draft ([transcription_standards.md](transcription_standards.md) §9). |
| 2026-07-29 | Default ASR `faster_whisper medium en`. A recording's two cameras share one audio feed, so audio stages run once per recording. |

### Why the cloud annotation desktop was abandoned (2026-09-23)

A cloud VM per annotator with ELAN pre-installed, reached over remote
desktop. VNC carries no audio; RDP (xrdp + PipeWire) did, but **audio wasn't
reliable under repeated looping of a segment**, which is how annotators work,
so it was torn out rather than patched. The code is gone (branch `labeling`
history has it). Two lessons that still apply: ELAN keeps a **per-medium
volume** in `.pfsx` files, and a kit must set it on every linked medium (one
bug had the `.wav` at volume 0); and the kit pattern of a fixed media path on
every machine (`CANONICAL_ROOT` in `make_kit.py`) avoids ELAN's "locate
media" dialog.

---

## Housekeeping

- Branches cleaned up 2026-11-16: only `main` and the working branch remain;
  merge work in through a PR, as before.
- Kate's own in-progress edits are sometimes left uncommitted: check
  `git status`, and never sweep someone else's edits into a commit.
