# Gold Annotation Guide — Transcription & Diarization References

How to produce the **gold reference data** the ASR benchmarking wing measures
against (pipeline doc §8). The benchmark is only ever as good as these
references, so this is careful human work, not a formality.

> **Scope:** we benchmark on *our* clinical audio, not public datasets — generic
> WER numbers don't transfer to this domain. That's the whole reason this guide
> exists.

---

## 1. What "gold" covers

Three separable reference layers; annotate the ones your metrics need:

| Layer | For metric | Output format |
|---|---|---|
| **Verbatim transcript** | WER / CER | plain text, one file per case |
| **Speaker segments** | DER / JER | RTTM (start, dur, speaker) |
| **Word/segment timing** | boundary error | CTM (word, start, dur) — optional |

Start with the transcript + speaker layers. Word-timing gold is expensive; only
build it if timing accuracy is a research question.

**None of the metrics you'll normally want require word-level timing:**
- WER/CER (`jiwer`) score plain text — no timestamps enter the calculation at all.
- DER/JER (`pyannote.metrics`) score speaker *turns* — RTTM is a turn-level format
  (`speaker, start, duration`), not a word-level one.

So the annotator's job is to get the words right and the speaker-turn boundaries
right — never to time individual words. Word-level timing (CTM) is a separate,
optional third layer, only worth the cost if you're specifically measuring
alignment/boundary accuracy.

---

## 2. Sampling — annotate a subset, not everything

You will **not** hand-annotate 300–400 videos. Pick a **stratified sample**
(start 5–10 cases) that spans the conditions that actually break ASR:

- quiet vs. noisy rooms
- number of speakers / overlap frequency
- speaker accents and speech rates
- audio source (which camera mic)

Record *why* each case was chosen. Report benchmark results as estimates over
this sample with the strata noted — don't imply the whole corpus is gold.

---

## 3. Transcription conventions (decide once, apply everywhere)

Inconsistency here silently inflates WER. Lock these before annotating:

- **Verbatim, not cleaned.** Transcribe what was said, including false starts and
  repetitions, unless a research reason says otherwise.
- **Disfluencies:** pick a policy for "um/uh" and *write it down*. (Common: keep
  them, since removing them hand-favors some engines over others.)
- **Overlapping speech:** annotate each speaker's words on their own speaker
  segment; mark overlap spans. Overlap is where diarization and ASR both fail —
  don't paper over it.
- **Inaudible / uncertain:** a fixed tag, e.g. `[inaudible]`, `[unclear: word?]`.
- **Non-speech:** `[laughter]`, `[phone rings]` — consistent bracketed tags.
- **Non-participant speech (hallway/room bleed):** some cases have intelligible
  speech leaking in from outside the room (hallway conversations, etc.) that a
  mic picks up and Whisper happily transcribes. Transcribe it verbatim like
  anything else audible (§3's "verbatim, not cleaned" applies here too — don't
  silently drop real audio content) — but put it on its own `OUTSIDE_ROOM`
  tier, never attributed to student/patient/preceptor. Then: **exclude the
  `OUTSIDE_ROOM` tier's text from `<case_id>.gold.txt`** (§7's WER export) —
  the benchmark should measure transcription of the clinical encounter, not
  whether the ASR happened to catch a stranger in the hallway — but **include
  its turn boundaries in `<case_id>.gold.rttm`** (labeled generically, not as
  one of the real participants). Diarization will likely still detect that
  voice as acoustically distinct regardless of what the gold says, so leaving
  it out of the RTTM entirely would falsely penalize a diarizer (via DER) for
  correctly noticing a real 4th voice.
- **Numbers, meds, jargon:** decide spelled-out vs digits ("fifteen" vs "15") and
  a canonical spelling list for domain terms (drug names, "mmHg", etc.). This
  directly affects the "nurse/nerds"-class error counting.
- **Casing & punctuation:** these are usually **normalized away** before WER
  (lowercase, strip punctuation — see `jiwer` transform in §8 of the pipeline
  doc). Annotate naturally; just know the scorer neutralizes them, so don't agonize.

Keep this convention list in `benchmarks/references/README.md` and version it.

---

## 4. Process — bootstrap, but beware anchoring

Two-stage, with a bias caveat:

1. **Machine draft.** Generate a WhisperX `.eaf` (`multidata.elan.build_eaf`,
   pipeline doc §7) as a starting point in ELAN. This saves enormous time on
   timing/segmentation — but note it's **word-level** (one annotation per word,
   one tier per speaker), because that's what got promoted from the working
   notebook code. You are not correcting it at that granularity (see 4a).
2. **Human correction in ELAN**, against the audio+video (see 4b for the
   step-by-step).

> **Anchoring risk:** correcting a Whisper draft biases the gold *toward Whisper*
> — the annotator tends to accept plausible-looking errors, flattering the very
> engine you're testing. Mitigations:
> - For the transcript layer specifically, prefer **correcting the diarization/
>   timing from the draft but re-typing the words from the audio**, or
> - have a **second annotator transcribe blind** (no draft) on at least the
>   inter-annotator subset (§5), and treat divergences as signal.

Never let a benchmark re-run overwrite a corrected file — save human gold under a
distinct name (`<case_id>.gold.txt`) and treat it as immutable.

### 4a. Work at utterance/turn level, not word level

The Whisper draft is word-level because that's the cheapest thing for the
*machine* to produce accurately. It is not the granularity you want a human
correcting. Timing every word by hand is slow, error-prone, and buys you
nothing: WER doesn't look at timing at all, and DER only needs speaker-*turn*
boundaries (RTTM is `speaker, start, duration` per turn, not per word).

So instruct annotators to:
- **Merge the word annotations on a tier into one annotation per speaker turn**
  (select the run of words spoken contiguously by one speaker, merge them into a
  single annotation spanning that turn — see 4b for the ELAN mechanics).
- **Retype the merged annotation's text** as the verbatim utterance, applying the
  §3 conventions (disfluencies, tags, etc.), rather than word-by-word editing.
- **Adjust the turn's start/end** only enough to bound the speaker's actual
  speech — second-level precision is plenty; don't chase word boundaries.

If a stretch has real overlap or fast speaker alternation, split it into as many
turns as there are distinct speaker segments — still no need to go word-level.

### 4b. Step-by-step in ELAN

1. **Open the draft.** File → Open, select the generated `<case_id>_<camera>.eaf`.
   If ELAN can't find the linked video (path moved since generation), it'll
   prompt you — point it at the local copy under `data/raw/`. The `<camera>`
   in the filename is always the case's canonical audio camera
   (`cases.audio_camera`, pipeline doc §5/§10) — there's only ever one draft
   `.eaf` per case, not one per camera. If you suspect the transcript is
   missing something only visible from the other camera's angle, that raw
   file is still on disk to check by hand; its audio is the same feed but its
   timestamps aren't guaranteed frame-exact against this one (encoder clock
   drift varies by camera pair — pipeline doc §5), so treat it as a
   supplementary look, not a synced second track.
2. **Show the waveform.** The draft links the isolated `.wav` alongside the
   video (not instead of it), so a waveform viewer added via the media panel
   controls shows real speech activity, not just a video-derived guess.
3. **Select a speaker's tier**, and step through its word annotations (Grid/Tier
   view makes this easiest — Edit → Grid or click through the tier in the
   annotation panel). Every draft already has empty `LEARNER`/`PATIENT`/
   `PRECEPTOR`/`ANNOUNCEMENT`/`OUTSIDE_ROOM` tiers ready to receive
   annotations (`elan.DEFAULT_TIERS`, pipeline doc §7) — the diarizer's own
   raw `SPEAKER_NN` tiers are what actually hold the machine-drafted words
   at this point, since diarization has no notion of clinical role.
4. **Merge words into a turn.** Select the contiguous run of word annotations for
   one uninterrupted turn, then use ELAN's Annotation → Merge (exact menu wording
   varies slightly by ELAN version — it's under the Annotation menu in 5.x)
   to collapse them into a single annotation spanning the turn.
5. **Retype the merged text** as the verbatim utterance while listening to the
   audio (loop playback around the selection to catch what Whisper missed).
6. **Fix speaker attribution.** Move each annotation from its raw `SPEAKER_NN`
   tier to the correct role tier (`LEARNER`/`PATIENT`/`PRECEPTOR`/
   `ANNOUNCEMENT`/`OUTSIDE_ROOM` — already there, no need to create them).
   Only create a new tier for a genuinely unanticipated party the five
   defaults don't cover.
7. **Mark overlap, disfluencies, inaudible spans, non-speech** per the §3
   conventions as you go.
8. **Repeat per tier** until every turn in the recording is a clean, retyped,
   correctly-attributed annotation.
9. **Save as the gold `.eaf`** under a distinct filename (never overwrite the
   machine draft) — e.g. `<case_id>_<camera>.gold.eaf`.
10. **Export to the benchmark formats** (§7): ELAN's File → Export As → Tab-
    Delimited Text (or Traditional Transcript Text) gets you the per-turn text
    and timing needed to build `<case_id>.gold.txt`. There's currently no
    built-in exporter straight to RTTM for `<case_id>.gold.rttm` — either export
    tab-delimited and reshape it, or ask about adding an `.eaf → gold` helper to
    `multidata.elan` (the reverse of `build_eaf`) if this becomes a recurring
    step.

---

## 5. Quality control — inter-annotator agreement

- **Double-annotate** a subset (e.g. 20% of cases) independently.
- Measure agreement: WER *between annotators* (transcript), DER between their
  speaker labels. High disagreement means your conventions (§3) are underspecified
  — fix them and re-annotate, don't average the confusion.
- The annotator-vs-annotator error rate is effectively your **noise floor**: no
  ASR engine can meaningfully "beat" it, so report it alongside engine WER.

---

## 6. Clinical data handling

- **De-identify transcripts:** replace patient names / identifiers with tags
  (`[PATIENT_NAME]`, `[DOB]`) per your IRB plan. Decide whether ASR is scored on
  the de-identified or raw text and be consistent (de-id spans usually excluded
  from WER).
- Gold files are still sensitive — they live under `data/`-equivalent protection,
  not casually in git if they contain PHI. Keep only de-identified references in
  the tracked `benchmarks/references/`.
- No reference gets processed without a `consent_ref` (pipeline doc §10).

---

## 7. File layout & how it feeds the benchmark

```
benchmarks/
├── references/
│   ├── README.md                       # the frozen convention list (§3)
│   ├── <case_id>.gold.txt           # verbatim transcript (WER)
│   ├── <case_id>.gold.rttm          # speaker segments (DER)
│   └── <case_id>.gold.ctm           # word timing (optional)
├── configs/                            # engine/param sweep definitions
└── results/
    └── runs.csv                        # append-only, provenance-stamped
```

`run_benchmark.py` (pipeline doc §8) loads `<case_id>.gold.txt`, runs the
hypothesis engine on the matching audio, computes WER/CER via `jiwer`, and appends
a provenance-stamped row to `results/runs.csv`. One gold file, many hypothesis
runs across models/params.

> **Current gap:** DER scoring against `<case_id>.gold.rttm` via
> `pyannote.metrics` is designed-for but not yet implemented in
> `run_benchmark.py` — today it only computes WER/CER. Collecting the RTTM layer
> now still isn't wasted effort (§4a's turn-level correction produces exactly
> what DER scoring needs later), but don't expect a DER number out of a
> benchmark run until that scoring path is added.
