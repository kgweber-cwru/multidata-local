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

1. **Machine draft.** Generate a WhisperX `.eaf` (pipeline doc §7) as a starting
   point in ELAN. This saves enormous time on timing/segmentation.
2. **Human correction in ELAN**, against the audio+video.

> **Anchoring risk:** correcting a Whisper draft biases the gold *toward Whisper*
> — the annotator tends to accept plausible-looking errors, flattering the very
> engine you're testing. Mitigations:
> - For the transcript layer specifically, prefer **correcting the diarization/
>   timing from the draft but re-typing the words from the audio**, or
> - have a **second annotator transcribe blind** (no draft) on at least the
>   inter-annotator subset (§5), and treat divergences as signal.

Never let a benchmark re-run overwrite a corrected file — save human gold under a
distinct name (`<case_id>.gold.txt`) and treat it as immutable.

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
hypothesis engine on the matching audio, computes WER/CER via `jiwer` (and DER via
`pyannote.metrics` against the `.rttm`), and appends a provenance-stamped row to
`results/runs.csv`. One gold file, many hypothesis runs across models/params.
