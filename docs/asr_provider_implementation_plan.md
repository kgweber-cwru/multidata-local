# ASR Provider — Implementation Plan

Step-by-step build order for [asr_provider_spec.md](asr_provider_spec.md).
Branch: `asr-provider-config`.

**This is a working checklist, not a standard.** Delete it when the work lands.
Every item has a *done when* so "in progress" can't hide.

---

## Three things that drive the ordering

**1. Annotation is the critical path, and it is not blocked on any of this.**
Gold references are human work measured in weeks. The template, standards, and
annotator guide all exist as of `a83e000`. **Phase 0 can start today**, in
parallel with everything else, and nothing downstream produces a number until it
finishes. If only one thing starts this week, it should be this.

**2. Verification gates the provider work.** The capability matrix
(spec §2) is explicitly unverified. Building adapters against it means building
against fiction — and the failure mode isn't a crash, it's a plausible-looking
benchmark that's quietly wrong.

**3. Adapters and gold are independent until scoring.** An adapter can be fully
validated on *shape* — does it emit a conforming normalized record — with no
gold at all. Only Phase 4 needs both. Don't serialize them.

---

## Phase 0 — Unblock (no code)

- [ ] **Start gold annotation on the first case.** Assign an annotator, walk
      them through [annotator_guide.md](annotator_guide.md), watch the first
      30 minutes over their shoulder.
      *Done when:* one `pass1.eaf` is frozen.
- [ ] **Validate the guide's ELAN mechanics against the installed version.**
      Menu wording and Segmentation Mode keystrokes drift between 6.x releases;
      the guide was written from general knowledge.
      *Done when:* someone has followed §1–§4 start to finish and corrected
      anything wrong.
- [ ] **Verify the capability matrix** against live vendor docs. Record the
      check date per row.
      *Done when:* spec §2's ⚠️ warning can be removed.
- [ ] **IRB position** on third-party processing of learner/preceptor voice.
      *Done when:* written answer on file.
- [ ] **Accounts + keys** for each provider; confirm no-train / zero-retention
      flags and BAA availability. Vendor defaults often permit retention.
      *Done when:* keys in `.env`, retention settings recorded in the registry.
- [ ] **Set `cloud_release`** on the hand-picked dev subset, with basis.
      *Done when:* `SELECT count(*) FROM cases WHERE cloud_release=1` is
      non-zero and every such row has a `cloud_release_basis`.

> **Nothing in Phase 1+ should start before the matrix is verified**, except the
> normalizer (§1.3), which is provider-independent.

---

## Phase 1 — Shared foundations

Provider-independent. All of it is testable without a single API call.

- [ ] **1.1 `benchmarks/configs/providers.yaml`** — capability registry per
      spec §2, plus a loader with validation.
      *Done when:* loader rejects a provider missing a required field, and
      every field in spec §2's table is represented.
- [ ] **1.2 `benchmarks/configs/glossary.yaml`** — schema (term, weight,
      category, pronunciation hint) and a hand-seeded starting set from known
      problem terms.
      *Done when:* file exists, loads, and content-hashes stably.
- [ ] **1.3 `multidata/normalize.py`** — the L0/L1/L2 profiles and shared
      transforms: casing, punctuation, whitespace, **bracket stripping**,
      number canonicalization, filler class.
      *Done when:* **tests pass** (see below).
- [ ] **1.4 Name redaction** — read `learner_name` / `sp_name` /
      `preceptor_name` from the manifest, fuzzy-match, apply to both sides, log
      every substitution.
      *Done when:* a known transcript redacts correctly and the log names each
      replacement.
- [ ] **1.5 Normalized record schema** — formalize the existing shape with
      `schema_version`, `word_timing`, `chunked`, and the `provenance` block
      (spec §4). A validator, not a class hierarchy.
      *Done when:* existing `faster_whisper` output validates unchanged.

> **The repo has no tests. Start them here.** `normalize.py` is deterministic,
> pure, and has the largest blast radius in the project — a bug in filler
> stripping silently corrupts every L1 number in every future run, and nothing
> would look wrong. Specifically test: backchannels survive L1 (`uh-uh` must
> never be stripped), fillers don't, `[...]` spans vanish from both sides,
> and L2 collapses repetitions without touching backchannels.

---

## Phase 2 — One provider, end to end

**Deepgram first.** It's the only candidate that exercises every capability at
once — internal diarization, word timings, `keyterm` glossary, and a real
`filler_words` switch. If the abstraction survives Deepgram, it will survive the
others; if it's wrong, Deepgram surfaces it cheapest.

- [ ] **2.1 Adapter contract** — the minimal protocol a provider module
      implements. Resist a base class until there are three implementations to
      generalize from.
- [ ] **2.2 Response cache** — key `(audio_sha256, provider, params_hash)`.
      Build this *before* the first adapter, not after; it's the difference
      between iterating for free and iterating on the meter.
      *Done when:* a repeat call with identical params makes no network request.
- [ ] **2.3 `--out-root`** — same relative layout, swapped root (spec §7). One
      writer serving both `data/` and `benchmarks/runs/<run_id>/`.
- [ ] **2.4 Deepgram adapter** — request construction, `cloud_release`
      enforcement, no-train flag, raw response persisted, normalization to the
      §4 record.
      *Done when:* `run_stage.py asr --engine deepgram --only <case>` produces
      a conforming record **and** refuses a case with `cloud_release=0`.
- [ ] **2.5 Glossary renderer for `keyterm`**, with lossiness recorded.
      *Done when:* over-limit glossaries truncate by weight and say so.
- [ ] **2.6 `--diarizer {provider,pyannote}`** control arm (spec §5).
      *Done when:* both arms produce records differing only in speaker labels.

> **Verify the interlock deliberately.** Point an adapter at a
> `cloud_release=0` case and confirm it refuses *before* any audio is read. A
> gate that fails open is worse than no gate, because it's trusted.

---

## Phase 3 — Gold export

Required, not optional — under the from-scratch workflow this is the only path
from annotator output to a scoreable reference. Blocked on Phase 0 producing an
`.eaf` to test against.

- [ ] **3.1 `.eaf` → `gold.txt` + `gold.rttm`** (`multidata.elan`, reverse of
      `build_eaf`): tier exclusion (standards §4), adjacent same-tier segment
      merge (§5), redaction (§8), L0 output.
      *Done when:* a real `pass1.eaf` round-trips and the RTTM loads in
      `pyannote`.
- [ ] **3.2 Reference layout** — Layer 1 under `data/gold/`, Layer 2 written to
      `benchmarks/references/`.
      *Done when:* Layer 2 contains no name from the manifest.

---

## Phase 4 — Scoring

Blocked on Phase 1 and Phase 3. First real numbers appear here.

- [ ] **4.1 Rework `run_benchmark.py`** — dual report (`wer_verbatim`,
      `wer_filler_neutral`, same for CER), per-run directory, the full
      provenance row from spec §8.
- [ ] **4.2 DER via `pyannote.metrics`** — the long-standing gap; RTTM gold
      finally exists to score against.
- [ ] **4.3 Inter-annotator agreement** as a first-class output — annotator-vs-
      annotator WER/DER, reported as the noise floor beside every engine number
      (standards §10).
      *Done when:* a results table shows the floor next to engine scores.

---

## Phase 5 — Remaining providers

Each is now a registry entry, an adapter, and a glossary renderer.

- [ ] **5.1 AssemblyAI** — `disfluencies` switch makes it the closest
      comparator to Deepgram.
- [ ] **5.2 Google STT v2** — hardest: GCS staging for long audio, and Chirp
      variants differ on whether diarization and adaptation coexist. Expect this
      one to stress the registry.
- [ ] **5.3 ElevenLabs Scribe v2** — likely no glossary mechanism; will be the
      first `glossary_mechanism: none` and the first real test of whether the
      lossiness reporting is honest.
- [ ] **5.4 Long-audio handling** — per-provider limits; if any adapter chunks,
      it must set `chunked: true` and record boundaries.

---

## Phase 6 — The bake-off

- [ ] **6.1 Sweep configs** — one axis at a time (pipeline doc §8c).
- [ ] **6.2 Headline run** — glossary-off, all providers, all gold cases.
- [ ] **6.3 Glossary-on sweep** — the "who best exploits domain hints" question.
- [ ] **6.4 Selection report** — WER/DER **beside `baa_available` and
      `cost_usd`**. A provider that can't be deployed doesn't win.

---

## Explicitly not in scope

Word-timing gold (CTM) · a provider-agnostic streaming path · multi-provider
production operation (the end state is one vendor) · prosodic annotation · any
retrofit of transcripts produced before the normalizer is versioned.

---

## Decisions this will surface

Flagged now so they're recognized as decisions rather than absorbed as
accidents:

- **Retry and rate-limit policy.** A retried request that returns different text
  breaks reproducibility. Recommend: retry only on transport errors, never on a
  successful-but-unexpected response, and record the attempt count.
- **What `params_hash` covers.** Too narrow and the cache serves stale results
  across a config change; too broad and it never hits. Recommend: every field
  sent to the vendor, nothing local.
- **Whether `medium` stays the local default** once cloud numbers exist. It was
  chosen for unattended batch throughput, not accuracy.
- **Cost ceiling** for the sweep before it runs, not after.
