# Project status

**Last updated: 2026-11-01.** Update this file whenever a working session
changes what's true; delete what stops being true rather than letting it
rot. History lives in git and in the decision log at the end.

---

## In one paragraph

The pipeline takes clinical-encounter video (OSCE simulated-patient
sessions, two PTZ cameras per exam room) to word-level transcripts with
speakers, ELAN files, prosody, camera sync, camera-view segmentation and 2D
body pose, all on the pilot's processing host (`tofino`). 57 cases (114 videos, ~75 h) are ingested,
all in the small exam rooms. Two threads are active: **validating
transcription** (gold annotation and an ASR bake-off) and **moving pose to
3D** (waiting on a room calibration session; see [pose_3d.md](pose_3d.md)).

---

## What's built, and how real it is

| Area | State |
|---|---|
| Ingest (`ingest_raw.py`, walkthrough notebook) | Working on real files; 2026-11-01 run added 4 cameras |
| audio, asr, diarize, elan | Run on every case. Re-run 2026-11-02 with `whisperx_disfluent large-v3` (keeps disfluencies) on pyannote community-1 diarization |
| prosody | Stage since 2026-11-01; run on all 57 cases (246 speakers) |
| sync | Every video; 5 rows fail (room 11's camera 31, 261041/41, a 2-min clip) and get no offset |
| views + preset names | Every video; 65 recurring views provisionally named, awaiting confirmation |
| pose (HALPE_26) | Full re-run 2026-10-30 to 11-01; the 4 newly added cameras follow |
| 3D | Not started: needs calibration ([pose_3d.md](pose_3d.md)) |
| ASR benchmarking | Built and tested (spec Phases 1-2); one gold reference so far |
| Cost tracking | Every stage run recorded in `stage_runs`; [cost_estimate.md](cost_estimate.md) |

---

## Transcription validation

**Gold:** one reference, **261498** (annotated by Kelli, adjudicated by Kate,
2026-10-28), in `benchmarks/references/`. Three earlier references (252848,
261456, 261473, all Kate's) skipped pass 2 and were withdrawn to pass 1 on
2026-10-28; their scores below are kept for the record only.

| Case (pass-1 reference) | Engine | WER | CER |
|---|---|---|---|
| 261456 | `whisperx` | 0.41 | 0.35 |
| 261456 | `whisperx_disfluent` | 0.29 | 0.25 |
| 261473 | `faster_whisper` | 0.41 | 0.35 |
| 261473 | `whisperx_disfluent` | 0.41 | 0.32 |

The disfluency-keeping config won on one case and not the other: no pattern
yet, and its settings are a first guess, never swept. **Next:** score all
local engines against 261498 (`python scripts/bench_status.py` shows the
empty cells), then more gold.

**Annotation** runs through Box: kits built on the processing host
(`annotation/make_kit.py`), shared per annotator, returned there; who has
which pass is in `annotation/assignments.csv` ([../annotation/README.md](../annotation/README.md)).
Annotators are scarce, so pass 1 can now start from a machine draft (route B,
standards §9): seeded gold adds cases, but engine rankings rest on blind gold.

**Cloud ASR providers:** IRB cleared sending audio to vendors for inference
(2026-09-10). Provider research lives in `benchmarks/configs/providers.yaml`.
Recommended order: Google (inside the institution's BAA, so no new paperwork)
for a first real number, AssemblyAI as the most informative (a real
disfluency switch). Adapters call vendor APIs with plain `requests`, not SDKs:
the raw response is the artifact to cache and compare.

### Before the first cloud call: two gaps

- **The `cloud_release` interlock doesn't exist in code.** `cases.cloud_release`
  is a column, but nothing reads it. Build the check (implementation plan
  4.2) before any cloud adapter (4.3), and test that it refuses a
  `cloud_release=0` case *before reading audio*. Then set `cloud_release=1`
  deliberately, with the IRB reference in `cloud_release_basis`, on the
  cases to send. Today 0 of 1,281 cases have it.
- **Per-request no-train / zero-retention flags are required**, not optional
  ([asr_provider_spec.md](asr_provider_spec.md) §9); vendor defaults often
  retain data. `consent_ref` is also empty on every case.

---

## Governance facts

- **Identifiable data.** The patient is an actor, but learners and
  preceptors are real and say their names; transcripts carry them, and the
  `cases` table holds real names. Never print names in shared output.
- **Storage:** the corpus may live in the institution's enterprise Google
  instance under BAA. A BAA covers a named list of services, not "Google"
  in general.
- **Vendor inference:** IRB-cleared (2026-09-10), subject to the two gaps
  above.
- **Pose:** HALPE_26 keeps 5 face points only, partly so the dataset can't
  identify faces. Demo clips blur faces by default (best effort: check before
  sharing).
- **Assessment use** (judging learners) would likely need its own IRB and
  consent coverage, separate from research use.
- **Open (noted 2026-11-03): names in public git history.** The first
  commit (`b3b248b`, 2026-07-29) included `manifest.sqlite` with full
  learner and SP names for 755 cases. The next commit deleted it, but it
  stays in history, and the repo was public until 2026-11-03 (no forks).
  Still to decide: purge history (`git filter-repo`, force-push every
  branch, ask GitHub to purge cached views) and whether to tell the IRB or
  privacy office.
- **Open: redaction gap.** `eaf_to_gold.py` redacts only the names recorded
  in `cases`. A case with preceptor speech but no `preceptor_name` exports
  the preceptor's spoken name unredacted: `261498.gold.txt` has one. Fix:
  refuse to export when a speaker tier has speech and no name to redact;
  then record 261498's preceptor and re-export it.

---

## Deliberately not built

So a fresh read doesn't mistake these for forgotten:

- A formal ASR adapter contract (plan Phase 3.1): engines are sibling
  functions until a cloud adapter needs more.
- A glossary -> Whisper prompt renderer (Phase 3.4): the loader exists.
- DER scoring against `.gold.rttm` (Phase 6.2), and wiring `normalize.py`'s
  L0/L1 profiles into `run_benchmark.py` (Phase 6).
- Number canonicalization in `normalize.py`: waiting for gold with numbers.
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
| 2026-11-01 | Draft-seeded gold (standards 1.1, route B) as a fallback when annotators are scarce: pass 1 corrects a fixed `whisperx_disfluent`/`large-v3` transcript, provenance recorded, seed engine never scored against its own seeded gold. |
| 2026-11-01 | Prosody is a pipeline stage (was notebook-only). Transcription Suite engine removed. Operators' preset names from Drew; recurring views labelled provisionally. Large rooms allowed again for transcription/2D; the 3D pilot stays small-room. No video trimming before pose. |
| 2026-10-30 | Pose restarted for 3D: HALPE_26, 8 slots, WholeBody files deleted; large-room data deleted; own 3D module with Pose2Sim as reference ([pose_3d.md](pose_3d.md)); every stage's cost recorded in `stage_runs`. |
| 2026-10-29 | Camera sync from the shared audio feed + per-frame timestamps; offsets never guessed. |
| 2026-10-26 | Everything on tofino, one manifest writer, nightly backup to `/wddata`; three drifted manifests merged (most advanced status wins). Pose output float16 `.npz`. |
| 2026-09-23 | Cloud annotation desktop abandoned; annotation through Box (below). |
| 2026-09-10 | IRB cleared vendor inference for ASR. |
| 2026-08-26 | Gold is annotated blind, from scratch, never by correcting a machine draft ([transcription_standards.md](transcription_standards.md) §9). |
| 2026-07-29 | Default ASR `faster_whisper medium en`. A case's two cameras share one audio feed, so audio stages run once per case. |

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

- Current work is on branch **`pose-3d-core`**, not yet merged to `main`.
- Merged and safe to delete when convenient: `asr-provider-config`,
  `benchmark-bookkeeping`, `annotation-standards-and-asr-providers`.
- Kate's own in-progress edits are sometimes left uncommitted: check
  `git status`, and never sweep someone else's edits into a commit.
