# ASR Provider Spec — multi-provider transcription & the selection bake-off

How any ASR service — local Whisper or a cloud vendor — plugs into this pipeline,
and how their outputs become comparable enough to pick a winner.

> **Purpose is selection, not federation.** The end state is *one* provider,
> deployed. So the abstraction has to make providers **comparable and
> swappable** — it does not have to make them simultaneously operable at scale.
> That constraint is what keeps this small: adapters register into
> `asr.ENGINES` and nothing else changes. There is no orchestration layer, and
> there shouldn't be one.

---

## 1. Settled decisions

Everything below follows from these. They were argued out before any code; if
you want to change one, change it here first.

| # | Decision |
|---|---|
| 1 | Cloud providers register into `asr.ENGINES` behind the existing `--engine` flag. `--engine <provider>` + `--model <model-id>` already generalizes (`faster_whisper`+`medium` → `deepgram`+`nova-3-medical`). No new axis. |
| 2 | Every adapter emits **the existing normalized record** (§4). Consumers never learn which provider produced it. |
| 3 | The **raw provider response is always cached** beside the normalized one. Never let the normalizer be the only copy. |
| 4 | Providers that diarize internally **do** diarize — but a **pyannote control arm** is available per provider (§5). |
| 5 | Scoring is **dual-report**: L0 verbatim and L1 filler-neutral (§6). |
| 6 | The **glossary is a swept axis**, not a fixed control. Headline cross-provider numbers run glossary-off (§3). |
| 7 | Testing writes under `benchmarks/runs/<run_id>/`; the default pipeline writes under `data/`. **Same layout, swapped root** (§7). |
| 8 | `cases.cloud_release` is a **safety interlock**, default false. No audio leaves this machine for a row that hasn't got it (§9). |
| 9 | **BAA availability is a selection criterion**, reported beside WER. A provider you can't deploy shouldn't win. |

---

## 2. The capability registry

`benchmarks/configs/providers.yaml` — versioned, hand-maintained, and the single
artifact that makes this whole wing legible. The scorer reads it to decide which
normalization profile applies; a human reads it to see at a glance *why* provider
X has no DER number.

Fields per provider:

| Field | Meaning |
|---|---|
| `diarizes_internally` | if false, `_diarize_and_merge` supplies speaker labels |
| `word_timings` | if false, records are `word_timing: false` and ELAN/CTM consumers refuse them |
| `glossary_mechanism` | `keyterm` \| `word_boost` \| `phrase_set` \| `initial_prompt` \| `none` |
| `glossary_limit` | max terms the mechanism accepts; renderer truncates by weight and logs it |
| `disfluency_param` | request param that keeps fillers, or null if the model can't |
| `speaker_hint_param` | how to pass expected speaker count |
| `formatting_off_param` | how to request unformatted output |
| `long_audio` | sync duration limit and the async/upload path |
| `baa_available` | selection criterion, not a footnote |
| `no_train_flag` | how to assert zero-retention / no-training on each request |
| `cost_per_minute` | non-trivial at 300–400 videos |

### Capability matrix — **unverified, needs a docs pass**

> ⚠️ **Do not build against this table.** It was written from knowledge current
> to roughly January 2026; AssemblyAI Universal-3 Pro and ElevenLabs Scribe v2
> are at or past that horizon, and every vendor here ships changes faster than
> this file gets edited. Verify each row against live documentation and record
> the date you checked it. Treated as fact, this table will silently corrupt the
> benchmark.

| Provider | Diarizes | Word timings | Glossary mechanism | Keeps fillers | Notes to verify |
|---|---|---|---|---|---|
| `faster_whisper` (local) | no — pyannote merged | yes | `initial_prompt` (lossy — prose, not terms) | inconsistently | baseline; fully pinned |
| `whisperx` (local) | yes (pyannote) | yes (wav2vec2 forced alignment) | `initial_prompt` | inconsistently | best local word timing |
| `deepgram` | yes (`diarize`) | yes | `keyterm` (Nova-3) | **yes** (`filler_words`) | Nova-3 Medical variant; BAA generally available |
| `google` | model-dependent | yes | `phrase_set` + boost | largely **no** | Chirp variants differ on diarization *and* adaptation; >1 min needs GCS |
| `assemblyai` | yes (`speaker_labels`) | yes | `word_boost` / keyterms prompt | **yes** (`disfluencies`) | Universal-3 Pro + Medical Mode unconfirmed |
| `elevenlabs` | yes (`diarize`) | yes | possibly none | partially | emits audio-event tags natively — see §6 bracket rule |

The **"keeps fillers"** column is the one with teeth: it drives which scoring
profile a provider can be fairly compared on (§6).

---

## 3. The glossary

One source file, per-provider renderers, honest lossiness.

You can standardize the *source*, never the mechanism — the vendors' term-biasing
systems differ in kind, not just in syntax. So:

**Source:** `benchmarks/configs/glossary.yaml` — term, optional weight, optional
category, optional pronunciation hint. Hand-curated: seeded with known problem
terms (the "nurse/nerds" substitution class, drug names, unit strings) and grown
as annotators hit real terms during gold transcription. Small, portable, no
external dependency, every addition a reviewable diff.

**Renderers:** one per `glossary_mechanism`. Where a mechanism drops information
— Whisper's `initial_prompt` can only carry prose, so weights vanish; a provider
with a term cap forces truncation — the renderer **records what it lost** in the
run provenance. A glossary that silently degraded is worse than no glossary,
because you'd attribute the difference to the model.

**Governance:** the file is versioned and its content hash (`glossary_sha`) is a
provenance column. Glossary version is an experimental variable exactly as
`initial_prompt` already is (pipeline doc §6a).

> **One file, three consumers.** This is the same list annotators spell domain
> terms from (transcription standards §6). Annotators spell from it, providers
> get biased toward it, the benchmark hashes it. Don't fork it.

**Why headline runs are glossary-off:** the mechanisms are too different to be
apples-to-apples with biasing on. Glossary-on is a separate sweep answering a
different and arguably more decision-relevant question — *which provider best
exploits domain hints* — and that answer matters most for the one you deploy.

---

## 4. The normalized record

Not a new schema. The existing one (pipeline doc §6), formalized and versioned:

```jsonc
{
  "schema_version": "1",
  "language": "en",
  "engine": "deepgram",
  "word_timing": true,          // false ⇒ ELAN and CTM consumers must refuse
  "chunked": false,             // true ⇒ boundaries recorded; stitched-timestamp risk
  "segments": [
    {
      "start": 12.34, "end": 15.67, "text": "...",
      "words": [
        {"word": "pressure", "start": 12.34, "end": 12.71,
         "score": 0.98, "speaker": "SPEAKER_00"}
      ]
    }
  ],
  "provenance": { /* §8 */ }
}
```

Four rules every adapter obeys:

1. **Raw response always written beside it** as `<...>.raw.json`. Fixing a
   normalizer bug then means re-deriving, not re-billing — and it lets someone
   with no API keys re-score every provider from cache.
2. **Never synthesize word timings.** A provider giving segment-level output only
   gets `word_timing: false`. It stays valid for WER and is refused by ELAN and
   CTM. Distributing words evenly across a segment invents data that looks real.
3. **Speaker labels normalized** to `SPEAKER_00` form regardless of what came
   back (`"A"`, `0`, `"speaker_0"`). `elan.format_speaker_name` already does part
   of this.
4. **Confidence is optional and never faked.** Providers that give none get
   `null`. A default of 1.0 would poison any downstream confidence filter.

**Caching:** key on `(audio_sha256, provider, resolved_params_hash)`. Re-scoring
must never re-bill.

---

## 5. Diarization and the control arm

Providers that diarize internally do so — that's the product you'd actually buy,
and it's what should be evaluated.

But it entangles two things: DER for `deepgram` scores Deepgram's diarizer, while
DER for `faster_whisper` scores pyannote. A WER difference between them could be
words *or* speaker assignment, and you can't tell which.

So: **`--diarizer {provider,pyannote}`**. The pyannote arm discards provider
speaker labels and merges the cached RTTM through the existing
`asr._diarize_and_merge` path. Free at inference — the RTTM is already cached per
case (pipeline doc §6).

Provenance carries `diarizer` as a **column distinct from `engine`**, so
"nova-3 + own diarizer" and "nova-3 + pyannote@version" are never confused.

Speaker-count hints (`speakers_expected` / `num_speakers` / `min`+`max`) are their
own axis. Pass them consistently or not at all — never per-provider ad hoc.

---

## 6. Text normalization and scoring profiles

The scorer is where provider differences get neutralized. All transforms are
deterministic, versioned (`normalizer_version` in provenance), and applied
**identically to reference and hypothesis**.

### Profiles

| Profile | Transform | Reported |
|---|---|---|
| **L0 verbatim** | casing, punctuation, whitespace, brackets, names, numbers | `wer_verbatim`, `cer_verbatim` |
| **L1 filler-neutral** | L0 + filled pauses removed | `wer_filler_neutral`, `cer_filler_neutral` |

Both numbers on every run. L0 asks "did it hear everything?"; L1 asks "did it hear
everything that carries meaning?" Providers that structurally can't emit fillers
(Google/Chirp) are penalized in L0 for a *formatting policy*, not for accuracy —
L1 is where they get a fair read.

> **L2 "clean" is a deliverable, not a metric** (transcription standards §7). It
> exists to hand readable transcripts to other users. Never score against it.

### The transforms

- **Bracket stripping** — all `[...]` spans removed from *both* sides. One rule
  covering a whole class of problems: provider audio-event tags (ElevenLabs emits
  these natively) stop being insertion penalties; `[unintelligible]` spans stop
  counting against anyone, which is correct — you can't score words nobody could
  hear.
- **Name redaction** — see §9. Both sides, before scoring.
- **Number canonicalization** — gold is transcribed *as spoken* ("one twenty over
  eighty"); providers format however they like. Canonicalize both sides, or you're
  benchmarking each vendor's formatter instead of its recognizer.
- **Filler class (L1 only)** — `uh`, `um`, `hmm`. **Backchannels are never
  stripped**: `mm-hmm`, `uh-huh`, `uh-uh`, `mm-mm`, `huh?` carry meaning. A patient
  answering "uh-uh" to "any chest pain?" is a clinical *no*; strip it and the L1
  transcript silently loses the answer.

**Request unformatted output** wherever a provider offers the choice
(`formatting_off_param`). Normalize once, in one shared module, rather than
absorbing six different vendor formatters.

---

## 7. Output layout and run identity

Same relative layout, swapped root — one writer, one `--out-root` flag. That's
what makes every tool usable from both paths without special-casing.

```
data/transcripts/<case_id>/<camera>_<engine>_<model>.json    # default pipeline
data/transcripts/<case_id>/<camera>_<engine>_<model>.raw.json

benchmarks/runs/<run_id>/                                    # testing
├── config.resolved.yaml     # every param actually sent, post-defaults
├── <case_id>.json           # normalized record
├── <case_id>.raw.json       # verbatim provider response
└── scores.json
```

`<run_id>` is timestamp + short config hash. `results/runs.csv` carries one
append-only row per run pointing at it.

**`benchmarks/runs/` is gitignored; only `results/runs.csv` is tracked.** Scores
and provenance travel in git; transcript text does not. See §9 — students
self-identify verbally, so raw responses carry real names. This fails safe and is
less machinery than a commit-time redaction gate.

---

## 8. Provenance

Every run appends a fully-attributed row. A benchmark you can't attribute to an
exact config is noise (pipeline doc §8d). Beyond the existing columns:

| Column | Why |
|---|---|
| `provider` | distinct from `model` |
| `provider_model_returned` | vendors silently update `nova-3` — record what they *say* they ran |
| `request_id` | the vendor's own handle, for support and for disputes |
| `diarizer` | `provider` or `pyannote@<version>` (§5) |
| `glossary_sha` | null for headline runs (§3) |
| `glossary_lossiness` | what the renderer dropped |
| `normalizer_version` | scores are only comparable within a version |
| `gold_sha` | hash of the reference actually used — gold gets revised, and a re-export silently invalidates old numbers |
| `wer_verbatim` / `wer_filler_neutral` | dual report (§6) |
| `cost_usd` | non-trivial at corpus scale |
| `cloud_release_basis` | why this case was eligible (§9) |

Local engines are pinned and reproducible. Cloud providers are **not** — a model
id is a moving target. `provider_model_returned` is the only defense, and it's
imperfect. Say so when reporting results.

> **Two numbers are only comparable if `normalizer_version` *and* `gold_sha`
> match.** Both change over time — the normalizer when §6 is revised, gold when
> a standards fix triggers a re-export. Neither failure is visible in the score:
> both numbers look fine, and the comparison is meaningless. The comparison tool
> checks this mechanically rather than trusting anyone to remember.

### Disfluency-tuning parity

Each provider's **`best_disfluency_config`** is recorded in the registry. If one
engine gets a hand-tuned prompt and another gets a single flag, the comparison is
rigged — so the fair comparison is every provider at its own documented best
effort, not at whatever effort it happened to receive. For Deepgram that's one
parameter; for Whisper it may be a prompt register plus a
`condition_on_previous_text` tradeoff. Both are "this provider, tuned."

---

## 9. Data handling

**Today's corpus is OSCE simulated-patient encounters.** The "patient" is an
actor, so there is no patient PHI. But the **learner and preceptor are real
people**, and two things follow:

1. **Their voices go to a vendor.** Not HIPAA, but identifiable human-subjects
   data. IRB coverage for third-party processing and an explicit no-train /
   zero-retention flag on **every** request are requirements, not preferences.
   Vendor defaults frequently permit retention.
2. **Students self-identify verbally** — "Hi, I'm Jamie, third-year." So
   transcript *text* carries real names even with a simulated patient.

### The interlock

`cases.cloud_release` (**to be added**), defaulting false. Cloud adapters refuse
any row without it. Today it sorts nothing — the corpus is uniformly OSCE. Its
job is to **already exist**, on the day real clinical data lands, so nothing
leaks because someone forgot to add a check later. Cheap now, impossible to
retrofit safely.

Store the *basis* alongside it, not just a boolean: `simulated_patient`,
`consented_third_party`, etc.

### Names

The redaction map is **already in the manifest**: `cases.learner_name` and
`cases.sp_name`. (`preceptor_name` needs adding.) No separate `names.csv`.
`manifest.sqlite` is gitignored, which is correct — it holds names.

Redaction is applied to **both** reference and hypothesis before scoring, so the
number you report belongs to text you can actually publish. It also stops you
measuring whether the ASR spelled "Jamie" vs "Jamey" — a real ASR weakness, but
not one you'd switch clinical vendors over, and pure noise in a
medical-terminology benchmark.

> **Known limitation:** ASR misspells names, so string-matching the hypothesis
> will miss some. Start with fuzzy matching plus a log of what was redacted. If
> residual leakage proves material, escalate to timestamp-window exclusion — the
> normalized records carry word timings, so hypothesis words overlapping a gold
> redaction span can be dropped regardless of spelling. **Don't build that until
> measurement says it's needed.**

### Clinical deployment

Would require a BAA with the selected provider and a storage story in a clinical
system. **Don't let that hypothetical constrain the research design now.** What
carries forward is the interlock and `baa_available` as a selection criterion.

---

## 10. Verification backlog

Before any of this is built:

- [ ] Verify every row of §2's capability matrix against live vendor docs; record check dates
- [ ] Confirm AssemblyAI Universal-3 Pro + Medical Mode availability and params
- [ ] Confirm ElevenLabs Scribe v2 — diarization, word timings, any keyterm mechanism
- [ ] Confirm which Google STT v2 / Chirp variants support diarization *and* adaptation together
- [ ] Confirm BAA availability and no-train flags per vendor
- [ ] Confirm long-audio limits — OSCE encounters exceed several sync limits
- [ ] Get IRB position on third-party processing of learner/preceptor voice
- [ ] Add `cases.cloud_release`, `cases.cloud_release_basis`, `cases.preceptor_name`
