# Transcription & Annotation Standards

The conventions that govern every gold reference in this project — what gets
transcribed, how it's written, and what happens to it afterward.

This is the **policy document**. For the step-by-step ELAN walkthrough, see
[annotator_guide.md](annotator_guide.md). For how these standards meet the ASR
benchmark, see [asr_provider_spec.md](asr_provider_spec.md).

> **Scope:** we benchmark on *our* audio — OSCE simulated-patient encounters —
> not public datasets. Generic WER numbers don't transfer to this domain. That's
> the entire reason this document exists.

> **Version this file.** Inconsistent conventions silently inflate WER and
> silently destroy inter-annotator agreement. Every change here invalidates
> comparisons across the boundary; see §12.

---

## 1. The governing principle

> **Every convention must be machine-removable without a human decision.**

This is what separates a markup scheme you can strip back to a clean transcript
from one you're stuck with forever. It's why prosody, emphasis, and speech-rate
notation are out of scope no matter how interesting they are: they can't be
removed mechanically, they have poor agreement, and no metric consumes them.

It also means the "clean transcript for other users" is a **derived artifact**,
never a separately-maintained file. One source, three renderings (§7).

---

## 2. What gold covers

| Layer | For metric | Output |
|---|---|---|
| **Verbatim transcript** | WER / CER | `<case_id>.gold.txt` |
| **Speaker turns** | DER / JER | `<case_id>.gold.rttm` |
| **Word timing** | boundary error | `<case_id>.gold.ctm` — *not built* |

Annotators produce the first two from a single ELAN file. **Nobody times
individual words by hand.** WER ignores timing entirely; DER needs turn
boundaries, not word boundaries. Word-level gold is a separate, expensive layer
worth building only if alignment accuracy becomes a research question in itself.

---

## 3. Sampling

You will not hand-annotate 300–400 encounters. Draw a **stratified sample**
(start 5–10 cases) spanning the conditions that actually break ASR:

- quiet vs. noisy rooms
- number of speakers and how often they overlap
- learner accent and speech rate
- audio source (which camera mic)

**Record why each case was chosen.** Report results as estimates over this sample
with the strata named — never imply the whole corpus is gold.

### Excerpts are valid references

A reference does **not** have to be a whole encounter. A carefully annotated
5-minute excerpt is a valid reference as long as scoring is restricted to that
span — WER is a rate, not a total.

This matters because annotator time is the binding constraint. For a fixed
budget, **six 5-minute excerpts across varied conditions beat two full 20-minute
encounters**: better strata coverage, more cases, same cost. Prefer breadth over
completeness unless a specific question needs whole-encounter continuity.

Record the span bounds with the reference, so scoring can restrict to it and so
nobody later mistakes an excerpt for a full transcript.

**Constraint:** gold annotation is human-local and could be done on any case, but
cross-provider scoring requires a case that's cloud-releasable
(`cases.cloud_release`, provider spec §9). Draw the sample from releasable cases
only, so every reference scores every engine. Today that's no constraint at all —
the corpus is uniformly OSCE — but note it before the pool stops being uniform.

---

## 4. Tiers

Six tiers, pre-created in the template. Annotators **never create tiers** except
for a genuinely unanticipated party.

| Tier | Holds | In `gold.txt`? | In `gold.rttm`? |
|---|---|---|---|
| `LEARNER` | the student | ✅ | ✅ |
| `PATIENT` | the standardized patient | ✅ | ✅ |
| `PRECEPTOR` | the supervising clinician | ✅ | ✅ |
| `ANNOUNCEMENT` | PA / station timing calls | ❌ | ✅ |
| `OUTSIDE_ROOM` | intelligible hallway bleed | ❌ | ✅ |
| `NOTES` | annotator observations | ❌ | ❌ |

**Why `ANNOUNCEMENT` and `OUTSIDE_ROOM` are in the RTTM but not the transcript:**
the benchmark should measure transcription of the *encounter*, not whether an
engine caught a stranger in the hallway. But a diarizer will detect those voices
as acoustically distinct regardless of what gold says — omitting them from the
RTTM entirely would falsely penalize a diarizer via DER for correctly noticing a
real extra voice. Transcribe them (§5's verbatim rule applies — don't silently
drop real audio), then exclude the text and keep the boundaries.

**`NOTES` is sparse and non-exhaustive.** Free text, no timing precision required.
It exists to answer "why did this case score badly?" — noise bursts, very quiet
stretches, heavy crosstalk, unfamiliar accents, equipment problems. It feeds error
analysis and sampling rationale at near-zero cost.

> **There is no noise tier, and no exhaustive event tier.** Noise enters neither
> metric, and the scorer's bracket-stripping rule (§7) already makes provider
> audio-event tags scoring-neutral. Exhaustive noise annotation is the classic
> scope creep that stalls annotation projects. `NOTES` serves the real need.

### Two things the tier scheme gives you for free

**Overlap needs no markup.** Annotators segment each role tier in its own
listening pass, so overlapping speech is simply two tiers carrying annotations at
the same timestamps. It's emergent from the geometry.

**There is no `SPEAKER_NN` intermediate.** Annotators segment directly onto role
tiers, because a human can hear who is talking. Gold RTTM labels are role names;
DER solves optimal label assignment anyway, so a diarizer's `SPEAKER_00` maps
cleanly at scoring time.

---

## 5. Segmentation

**Unit: the utterance / breath group** — a natural phrase boundary, typically a
few seconds. Not the full speaker turn.

A 90-second history-taking summary as one annotation is unwieldy to type, to
review, and to compare between annotators. Utterance-level segments are far
easier to work in ELAN's Transcription Mode and give finer-grained
inter-annotator comparison.

Turn-level RTTM is produced by **mechanically merging adjacent same-tier
segments** at export. Deterministic, costs nothing.

**Boundary precision:** bound the speech, don't chase it. Roughly a tenth of a
second is plenty. Nothing downstream needs better, and precision you can't
reproduce is worse than none.

**Silent pauses are never marked.** Utterance timestamps already measure them in
milliseconds — continuous, not categorical, and free. Pause duration is the
strongest hesitation signal in clinical communication research and you get it
without a single keystroke.

---

## 6. Transcription conventions

**Verbatim, not cleaned.** Transcribe what was said. False starts, repetitions,
grammatical errors, and self-corrections all stay. The cleaned rendering is
derived later (§7) — cleaning at the keyboard is irreversible.

### Filled pauses — semantically empty, removed at L1

`uh` · `um` · `hmm`

> **`er` and `erm` are deliberately excluded.** They're British-convention
> variants, and `uh` vs `er` is perceptually a coin flip for American speakers —
> pure inter-annotator noise for no analytic gain. Write `uh`.

### Backchannels — carry meaning, never removed at any level

| Token | Means |
|---|---|
| `mm-hmm` | yes |
| `uh-huh` | yes |
| `uh-uh` | no |
| `mm-mm` | no |
| `huh?` | request for repetition |

> ⚠️ **`uh-huh` and `uh-uh` are one letter apart with opposite meanings.** A
> patient answering "uh-uh" to "any chest pain?" is a clinical *no*. This is a
> genuine annotator hazard and ASR providers confuse the pair constantly —
> expect "provider flips patient yes/no backchannels" to become a real finding.
>
> **Discriminator:** `uh-uh` has a glottal stop between syllables and falls then
> rises. `uh-huh` has an audible /h/ and rises. **If you can't tell, listen to
> what comes next** — the other speaker's response disambiguates almost every
> time. Still unsure? Write `[unintelligible: uh-huh?]`. A flagged unknown is
> recoverable; a flipped clinical *no* is not.

### Prolongation — trailing colon

`so:` · `the:` · `I: think`

**One colon regardless of length.** Never grade duration — that destroys
agreement for information already measurable from the waveform.

### Fragments and cutoffs — trailing hyphen

`par- particular` · `I was gon-` · `I went- I drove`

The strongest stammer and self-repair signal, and standard LDC/Switchboard
convention. Self-repair needs no additional markup — the hyphen already marks the
abandonment.

### Repetitions — no markup at all

Just transcribe them: `I I I went`. Adjacent identical tokens are mechanically
detectable, so L2 collapses them automatically. Zero annotator burden, full
recoverability.

### Annotator uncertainty

| Tag | Use |
|---|---|
| `[unintelligible]` | nothing recoverable |
| `[unintelligible: pressure?]` | a best guess worth preserving |

Both are stripped from *both* sides before scoring (§7), so uncertain spans never
count for or against an engine. Use them freely — guessing is the expensive
mistake.

### Vocalizations — optional, non-exhaustive

`[laugh]` · `[cough]` · `[sigh]` · `[throat clear]`

Tag them when clinically interesting; skip them otherwise. Scoring-neutral either
way.

### Numbers and units — as spoken

`one twenty over eighty`, not `120/80`. `fifteen milligrams`, not `15 mg`.

Providers' number formatting varies wildly; canonicalizing both sides at scoring
time measures the recognizer instead of the vendor's formatter. Writing digits at
the keyboard throws away what was actually said.

### Domain terminology — spell from the glossary

`benchmarks/configs/glossary.yaml` is the canonical spelling list. It is **the
same file** the ASR providers get biased toward (provider spec §3). Hit a term
that isn't in it? Add it — that's how the glossary grows.

### Casing and punctuation — annotate naturally

The scorer normalizes both away. Punctuate for readability and move on; don't
agonize.

### Explicitly out of scope

Prosody · emphasis · intonation contours · overlap markers · speech rate.

Overlap is already emergent from tier geometry (§4). The rest violate §1 — they
can't be stripped mechanically, they have poor agreement, and nothing consumes
them.

---

## 7. Normalization levels

One source, three renderings. Each is a deterministic transform of the one above.

| Level | Contents | Purpose |
|---|---|---|
| **L0 verbatim** | everything in §6 | the gold file; **immutable** |
| **L1 filler-neutral** | L0 − filled pauses, prolongation marks, bracketed spans | second WER number |
| **L2 clean** | L1 − fragments, − collapsed repetitions | readable transcript for others |

**L0 and L1 are scoring profiles. L2 is a deliverable, never a metric.**

Shared transforms applied identically to reference and hypothesis: casing,
punctuation, whitespace, **bracket stripping**, name redaction (§8), number
canonicalization.

> **Known limitation:** L2 drops the fragment token but *not* the whole abandoned
> phrase — `I went- I drove` becomes `I went I drove`, not `I drove`. Removing
> abandoned material properly requires a judgment call about where the repair
> started, which violates §1. Stated honestly rather than approximated badly.

### A research affordance

**L0 minus L1 is a disfluency measure.** Filled-pause rate, fragment rate, and
pause duration per learner turn fall straight out of the gold files with no
additional annotation — so learner fluency becomes analyzable as a by-product of
building ASR references.

---

## 8. Names and de-identification

**Annotators type names verbatim, exactly as heard.** They make no redaction
decisions at the keyboard.

This is deliberate, and it reverses the intuitive approach. The reasoning: **the
hypothesis transcripts contain the real names regardless** — the provider returns
"Jamie" and that response lands on disk. Redacting only the gold file protects
nothing and creates a reference/hypothesis mismatch that scores as an error
against a provider that was *correct*. And fixing that requires a name list
anyway. Since the list is needed either way, use it once, mechanically.

### Two layers

**Layer 1 — protected working layer** (`data/`, gitignored): ELAN files with real
names, raw provider responses, normalized transcripts. Scoring happens here.

**Layer 2 — publishable layer** (`benchmarks/references/`, tracked): the
de-identified gold export, produced **mechanically** from Layer 1. Never
hand-edited.

The redaction map is **already in the manifest** — `cases.learner_name`,
`cases.sp_name` (and `preceptor_name`, to be added). No separate name file.

Redaction is applied to both reference and hypothesis before scoring, so the
reported number belongs to text you can actually publish.

**Why this is better:** redaction becomes a logged deterministic transform rather
than an unauditable human judgment made mid-flow; Layer 2 is derived, so it can
be regenerated; and annotators just transcribe.

> **Handling caution:** Layer 1 ELAN files are the most sensitive artifacts an
> annotator touches. They stay on managed storage, are never emailed, and never
> enter git.

---

## 9. Process — blind first

Annotation is **from scratch**. No machine draft is corrected, ever.

Correcting a Whisper draft biases gold *toward Whisper*: annotators accept
plausible-looking errors, flattering the very engine under test. The bias is
invisible in the output and fatal to the benchmark's purpose.

### Pass 1 — blind

The annotator works from the template and the audio alone. No draft, no existing
transcript, no other annotator's work. Output is `<case_id>.pass1.eaf`, which is
**frozen and immutable** on completion.

### Pass 2 — adjudication

Only after pass 1 is frozen may the machine draft be consulted. Divergences flag
spots to **re-listen** — never to accept. The reviewer's job is to decide what
the audio actually contains, not to reconcile two documents. Output is
`<case_id>.gold.eaf`.

Because pass 1 is preserved, the human-vs-machine divergence rate is a free
signal: a systematic pattern in it usually means either a convention gap (§12) or
a genuine engine weakness worth reporting.

**Never let a pipeline re-run overwrite human work.** Gold files carry distinct
names and are treated as immutable.

---

## 10. Quality control

**Double-annotate a subset** (target ~20%) fully independently — two blind pass 1s
on the same case, neither annotator seeing the other's work.

Measure agreement: WER *between annotators* for transcript, DER between their
speaker labels. Report at both L0 and L1 — L0 disagreement concentrated in
disfluency markup means a convention problem, not a perception problem.

**High disagreement means §6 is underspecified.** Fix the standard and
re-annotate. Do not average the confusion.

> **The annotator-vs-annotator error rate is your noise floor.** No engine can
> meaningfully "beat" it. Report it alongside every engine WER — a benchmark
> without it can't distinguish a good model from a saturated metric.

### When there's only one annotator

With a single annotator there is **no noise floor at all**. That's a realistic
situation here, so name the options rather than letting it slide:

1. **Second-annotate one excerpt yourself.** Five minutes is enough for *an*
   estimate, and a rough floor beats none.
2. **Report engine numbers with an explicit statement that no floor was
   established.**

Do not quietly omit it. The absence of a floor is a limitation on every number in
the report, and it belongs next to them.

---

## 11. File layout

```
elan/
└── template.etf                        # the versioned annotation template

data/gold/<case_id>/                    # GITIGNORED — Layer 1, real names
├── <case_id>.pass1.eaf                 # frozen blind pass
└── <case_id>.gold.eaf                  # adjudicated

benchmarks/
├── configs/
│   ├── glossary.yaml                   # canonical spellings + provider biasing
│   └── providers.yaml                  # capability registry
├── references/                         # TRACKED — Layer 2, de-identified
│   ├── <case_id>.gold.txt
│   └── <case_id>.gold.rttm
├── runs/                               # GITIGNORED — per-run outputs
└── results/
    └── runs.csv                        # TRACKED — append-only, provenance-stamped
```

> **The template is tracked in git** (`elan/template.etf`), not under `data/` —
> it's a standard, not data, and an untracked template gives no guarantee that
> two annotators are using the same tier set. Changing its tiers is a standards
> change (§12), not a file edit.
>
> An older copy may still exist at `data/template.etf` from before the move. It
> carries a leftover `default` tier and no `NOTES` tier. **Delete it** — two
> competing templates is exactly the ambiguity this layout removes.

### Current gaps

- **No `.eaf → gold` exporter exists.** Under the from-scratch workflow this is
  no longer optional — it's the only path from annotator output to
  `gold.txt` + `gold.rttm`. It must apply tier exclusion (§4), merge adjacent
  same-tier segments (§5), and redact from the manifest (§8).
- **DER scoring is not implemented** in `run_benchmark.py` — it computes WER/CER
  only. Collecting the RTTM layer is not wasted (§5 produces exactly what DER
  needs), but don't expect a DER number until that path exists.

---

## 12. Change control

Changing §6 or §7 **invalidates comparisons across the change**. So:

1. Every change gets a version bump and a dated entry below.
2. Gold files record the standards version they were annotated under.
3. `normalizer_version` in the benchmark provenance moves with §7.
4. Existing gold is **not** silently re-interpreted under new rules — either
   migrate it deliberately and say so, or scope results to a version.

The most common trigger is §10 finding disagreement that traces to an
underspecified rule. That's the system working: fix the rule, bump, re-annotate
the affected subset.

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-08-26 | Initial standard. From-scratch blind annotation, utterance-level segmentation, L0/L1/L2 levels, verbatim names with mechanical redaction. Supersedes `gold_annotation_guide.md`. |
