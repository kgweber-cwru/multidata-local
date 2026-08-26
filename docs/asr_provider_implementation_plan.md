# ASR Provider — Implementation Plan

Step-by-step build order for [asr_provider_spec.md](asr_provider_spec.md).
Branch: `asr-provider-config`.

**This is a working checklist, not a standard.** Delete it when the work lands.
Every item has a *done when* so "in progress" can't hide.

---

## Three things that drive the ordering

**1. Annotation is the critical path, and it is not blocked on any of this.**
Gold references are human work measured in weeks. The template, standards, and
annotator guide all exist as of `a83e000`. **Phase 0 starts today**, in parallel
with everything else. If only one thing starts this week, it should be this.

**2. Build outward from what already works.** `faster_whisper` is already in
`ENGINES` and already emits the record shape, so making it the first provider
under the new abstraction is a *refactor validation*: it proves the abstraction
can express something known-good before it's used to express something new. It
also needs no keys, no IRB, no budget, and no interlock — so it can't be blocked.

**3. One new dimension at a time.** Whisper validates the **shared layer**
(record schema, normalizer, glossary source, cache, `--out-root`). Deepgram then
validates the **cloud layer** (auth, interlock, keyterm renderer, rate limits).
Bundling both into one first provider means that when something breaks you don't
know which layer broke.

> Adapters and gold are independent until Phase 5. An adapter validates on
> *shape* — does it emit a conforming record — with no gold at all. Don't
> serialize the two tracks.

---

## Phase 0 — Unblock

Annotation and the Whisper disfluency sweep both start here. Neither needs a
vendor, a key, or a gold file.

- [x] **0.1 Start gold annotation on the first case.** Assign an annotator, walk
      them through [annotator_guide.md](annotator_guide.md), watch the first
      30 minutes over their shoulder.
      *Done when:* one `pass1.eaf` is frozen.
- [x] **0.2 Validate the guide's ELAN mechanics** against the installed version.
      Menu wording and Segmentation Mode keystrokes drift between 6.x releases.
      *Done when:* someone has followed §1–§4 start to finish and fixed anything
      wrong.
- [ ] **0.3 Reference-free filler sweep.** Count filler tokens per minute across
      Whisper configs — `initial_prompt` register, `condition_on_previous_text`,
      model size. **Needs no gold**, so it runs during the annotation window.
      *Done when:* a table of config → fillers/min exists, with a 2-minute
      sample eyeballed per config to confirm they're real and not hallucinated.
      **The tool to run this now exists** (`asr.transcribe_whisperx_disfluent`,
      Phase 3.3) with a starting config — this item is about actually running
      the sweep against real audio and picking a winner, not building the
      engine.
- [ ] **0.4 Verify the capability matrix** against live vendor docs; record the
      check date per row. *Gates Phase 4 only.*
      *Done when:* spec §2's ⚠️ can be removed.
- [ ] **0.5 IRB position** on third-party processing of learner/preceptor voice.
- [ ] **0.6 Accounts + keys**; confirm no-train flags and BAA availability.
      Vendor defaults often permit retention.
- [ ] **0.7 Set `cloud_release`** on the dev subset, with basis recorded.

> **0.3 is the experiment that motivated this ordering.** Whisper's cleaning
> behavior is learned — subtitle-derived training data strips
> disfluencies, and the prior is strong. Expect a low ceiling. **Finding the
> ceiling is the finding**: "the local default structurally cannot produce
> verbatim clinical transcripts, and here is the sweep proving it" is exactly
> what justifies the multi-provider benchmark. L1 already exists as the fair-read
> profile for engines that can't emit fillers.
>
> Note the tension: `condition_on_previous_text=False` is the main defense
> against hallucination loops ([asr.py:131](../src/multidata/asr.py#L131),
> pipeline doc §6a). Turning it on is also what propagates disfluent style.
> Measure both; don't assume you can have both.

---

## Phase 1 — Shared foundations

Provider-independent. All testable without a single API call.

- [x] **1.1 `benchmarks/configs/providers.yaml`** — capability registry per
      spec §2, plus a validating loader (`multidata/providers.py`).
      *Done:* loader rejects any entry missing a required field, and reports
      every bad entry at once rather than stopping at the first. **Cloud rows
      in the file are still an unverified draft** (0.4 is unstarted) — the
      loader accepts them structurally; nothing here checks them against
      reality. `test_local_providers_match_asr_engines_exactly` pins the
      registry and `asr.ENGINES` to the same set so they can't silently drift.
- [x] **1.2 `benchmarks/configs/glossary.yaml`** — schema and a hand-seeded set
      from known problem terms, plus a loader (`multidata/glossary.py`).
      *Done:* loads, content-hashes stably (whitespace/comment-proof), hash
      changes on any real edit. Seed list is illustrative examples only, per
      the file's own header — needs growing from real annotation.
- [x] **1.3 `multidata/normalize.py`** — L0/L1/L2 profiles and shared
      transforms: casing, punctuation, whitespace, **bracket stripping**,
      filler class. *Done:* **tests pass** (`tests/test_normalize.py`, 40+
      cases). Caught two real bugs before landing: `BACKCHANNELS` had
      `"huh?"` in a form punctuation-stripping made unreachable, and the fuzzy
      name-match threshold (redact.py, below) was too strict to catch
      "Jamie"/"Jamey" (ratio 0.80 < the original 0.82 cutoff). **Number
      canonicalization is explicitly deferred**, not silently skipped — see
      `NUMBER_CANONICALIZATION_TODO` in the module. Blind rule-writing before
      any gold transcript has real numeric content in it risks confident
      rules for formats nobody actually uses here; revisit once Phase 5 gold
      exists.
- [x] **1.4 Name redaction** (`multidata/redact.py` + `manifest.names_for_case`)
      — fuzzy-matched against the manifest's name columns, tag-based
      (`[LEARNER_NAME]` etc.) so it passes straight through the existing
      bracket-stripping rule for free. *Done as a tested primitive* — tests
      pass; **wiring it into an actual scoring run is Phase 6**, not done yet.
- [x] **1.5 Normalized record schema** (`multidata/records.py`) —
      `finalize_record`/`validate_record` with `schema_version`, `word_timing`,
      `chunked`, `provenance` (spec §4). A validator, not a class hierarchy.
      *Done:* the real `transcribe_faster_whisper` shape (pre-diarization-merge,
      no `speaker` on words yet) validates unchanged — `validate_record` is
      deliberately lenient about *which pipeline stage* a record is at, not
      just its final shape.

> **Tests now exist** (`tests/`, 109 passing) — see `tests/README.md` for what
> they do and don't cover. `normalize.py` and `redact.py` got the most
> scrutiny per the plan's own reasoning: both are deterministic, pure, and
> have the largest blast radius in the project. The two real bugs caught
> above are exactly the "nothing looks wrong" failure mode this was meant to
> catch — both were silent until a parametrized test forced every value in
> `BACKCHANNELS`/a real misspelling through the code.

---

## Phase 2 — Bookkeeping

**Build this before there are many runs, not after.** It is the difference
between a sweep you can read and a directory you're afraid of. See
[Run bookkeeping](#run-bookkeeping) below for the design.

- [x] **2.1 Run identity + layout** (`multidata/bench_runs.py`) —
      `benchmarks/runs/<run_id>/` holding `config.resolved.yaml`, `record.json`
      (the normalized record), and `scores.json`. `run_id` is
      `<UTC timestamp>_<engine>_<model>_<8-char config hash>`.
      *Done:* `ls benchmarks/runs/` reads as a log, confirmed by real smoke
      test. A separate `raw.json` per spec §4 isn't written yet — today's
      local engines have no response distinct from their normalized record;
      a cloud adapter should write one into the same directory once one
      exists.
- [x] **2.2 Response cache** (`bench_runs.cache_key`/`cache_get`/`cache_put`,
      wired into `run_benchmark.py`'s live path) — key
      `(audio_sha256, provider, params)`, on by default (`--cache-root`),
      with `--no-cache` as an escape hatch for a genuine timing measurement.
      *Done:* verified via real smoke test — a repeated identical call made
      zero engine calls (`source: cache_hit`, `wall_s: 0.0`), a changed
      model made a fresh one.
- [x] **2.3 `--out-root`** — done as "the testing tool's output root is a
      flag, not hardcoded," not as literally sharing run_stage.py's writer
      function. The two tools' layouts don't cleanly unify (production is
      per-case/camera, testing needs many runs to coexist per case) and
      forcing them into one function would cost more than it returns; what
      they *do* share is `asr.transcribe()` and `records.validate_record()`
      underneath. Narrower than spec §7's original phrasing — noted here so
      the gap is visible, not silently reinterpreted.
- [x] **2.4 `gold` table in the manifest** — `id` (not `case_id`) is the
      primary key: excerpt gold means several rows can share a `case_id`
      (standards §3). `eaf_to_gold.py` now records a row on every successful
      export. *Done:* `manifest.all_gold()` answers "which cases have gold"
      as a query.
- [x] **2.5 `scripts/bench_status.py`** — gold references (cross-referencing
      the manifest **and** disk, in both directions), latest score per
      (case, engine, model), and empty matrix cells against local engines.
      *Done:* running it against the real repo immediately surfaced a real
      drift — case 261456's `.gold.txt`/`.gold.rttm` existed with **no**
      manifest row, because they were exported before this session's
      gold-table wiring existed. Backfilled by hand (`manifest.record_gold`)
      once found. Exactly the kind of silent gap this tool exists to catch,
      caught on its first real run.

> **A real usage bug surfaced and got fixed along the way, twice:**
> `run_benchmark.py`'s `--transcript` mode silently recorded `faster_whisper`
> for a transcript with no `engine` field and no `--engine` passed (case
> 261456's first two runs) — fixed to refuse instead of guess. And `RESULTS`
> (the runs.csv ledger) had no override flag, so two rounds of manual smoke
> testing this phase wrote synthetic rows into the real ledger before
> `--results` was added and both incidents cleaned up. Neither was caught by
> a test until it happened for real — worth remembering next time a "surely
> nobody would do that" gap looks safe to skip.

---

## Phase 3 — Whisper as the first provider

The shared layer, end to end, at zero marginal cost.

> **Pulled forward, out of order, on request:** the disfluency-preservation
> engine below (3.5's `whisperx_disfluent`) landed during the Phase 1 push
> rather than waiting for 3.1–3.4, since it's local, needs no keys/IRB/budget,
> and is exactly the tool Phase 0.3's sweep needs to exist before it can run.
> No ordering violation — it's provider-independent, same reasoning as
> Phase 0's parallelism.

- [ ] **3.1 Adapter contract** — the minimal protocol a provider implements.
      Resist a base class until there are three implementations to generalize
      from. **Deliberately still not built**: today's `whisperx_disfluent`
      addition used the existing sibling-function pattern
      (`transcribe_faster_whisper`/`transcribe_whisperx`/`transcribe_suite`)
      rather than introducing a protocol, per this exact "resist a base class"
      note. Revisit once a cloud adapter (Phase 4) needs shape enforcement
      that four sibling functions can no longer provide informally.
- [ ] **3.2 Port `faster_whisper` onto it** — blocked on 3.1 (no adapter
      contract to port onto yet). **Not the same as** the device-portability
      change already made to `transcribe_faster_whisper` (now routes through
      `device.best_ct2_device`/`best_ct2_compute_type` instead of hardcoding
      cpu/int8) — that change needed no contract, it's still the same
      function shape.
- [ ] **3.3 Disfluency configuration** — `asr.transcribe_whisperx_disfluent`
      exists with a **starting** config (`condition_on_previous_text=True` +
      `DISFLUENCY_PROMPT`, written in-register rather than describing the
      register), registered in `providers.yaml` as
      `whisperx_disfluent.best_disfluency_config`. **This is a hypothesis, not
      the swept winner** — Phase 0.3's actual reference-free sweep (fillers/min
      across configs, eyeballed) hasn't run yet. Don't treat the registry
      entry as validated until it has.
- [ ] **3.4 Glossary renderer for `initial_prompt`** — not built. The glossary
      *source* (`glossary.yaml`) and its loader exist (1.2); turning its terms
      into an actual prompt string for Whisper does not yet.
- [x] **3.5 `whisperx` as a second registry entry** — done, and exceeded: the
      registry now has `whisperx` (hallucination-avoidance-tuned, unchanged
      behavior) **and** `whisperx_disfluent` (new) as separate entries sharing
      `glossary_mechanism: initial_prompt`, plus `faster_whisper` and `suite`.
      `test_local_providers_match_asr_engines_exactly` proves the registry and
      `asr.ENGINES` agree on the full local set, not just this one pair.

---

## Phase 4 — Deepgram and the cloud layer

Gated on 0.4 (matrix verified) and 0.6 (keys). Everything new here is
cloud-specific.

- [ ] **4.1 Credentials** from `.env`; never in configs or the manifest.
- [ ] **4.2 `cloud_release` enforcement** in the adapter path.
- [ ] **4.3 Deepgram adapter** — request construction, no-train flag, raw
      response persisted, normalization to the §4 record.
      *Done when:* `run_stage.py asr --engine deepgram --only <case>` produces a
      conforming record.
- [ ] **4.4 Glossary renderer for `keyterm`** — first weighted mechanism.
- [ ] **4.5 `--diarizer {provider,pyannote}`** control arm (spec §5).
      *Done when:* both arms differ only in speaker labels.

> **Verify the interlock deliberately.** Point the adapter at a
> `cloud_release=0` case and confirm it refuses *before any audio is read*. A
> gate that fails open is worse than no gate, because it gets trusted.

---

## Phase 5 — Gold export

Blocked on Phase 0 producing an `.eaf`. Required, not optional — the only path
from annotator output to a scoreable reference.

- [x] **5.1 `.eaf` → `gold.txt` + `gold.rttm`** (`elan.export_gold`,
      `scripts/eaf_to_gold.py`) — tier exclusion (standards §4), adjacent
      same-tier merge (§5), redaction via `manifest.names_for_case` (§8).
      *Done:* verified on a real `pass1.eaf` (case 261456) with real
      redaction (`[LEARNER_NAME]` in the actual output), and with a
      synthetic round-trip test suite (`tests/test_elan_export_gold.py`).
      Landed before this phase's own numbering caught up to it — see the
      `asr-provider-config` branch history.
- [x] **5.2 Excerpt support** — `manifest.record_gold`'s
      `span_start`/`span_end`, and `run_benchmark.py --span-start/--span-end`
      trims the *hypothesis* to that window before scoring (otherwise every
      word outside the excerpt is a spurious insertion against a reference
      that only covers it). **Scope actually delivered:** this records and
      scores against a span; it does **not** filter a fully-annotated `.eaf`
      down to a sub-window at export time. The assumed use case is an
      annotator segmenting only the excerpt's window in the first place, so
      there's nothing outside it to filter. Extracting a slice from an
      already-fully-annotated file is unbuilt — flag it if that scenario
      actually comes up.
- [x] **5.3 Layer 2 written to `benchmarks/references/`.**
      *Done:* confirmed on the real 261456 output — no learner/patient/
      preceptor name present, `[LEARNER_NAME]` in its place.

---

## Phase 6 — Scoring

Blocked on Phases 1, 2, and 5. First real numbers.

- [ ] **6.1 Rework `run_benchmark.py`** — dual report, per-run directory, full
      provenance row including **`gold_sha`**.
- [ ] **6.2 Comparability guard** — refuse (or loudly warn) when comparing rows
      with different `normalizer_version` or `gold_sha`.
- [ ] **6.3 Per-case reporting, not just means.** With n≈5, a mean hides
      everything; per-case is where "this config helps case 1 and hurts case 2"
      becomes visible.
- [ ] **6.4 DER via `pyannote.metrics`** — the long-standing gap.
- [ ] **6.5 Noise floor** — annotator-vs-annotator WER/DER beside every engine
      number, *or* an explicit statement that there's only one annotator and no
      floor exists (standards §10).

---

## Phase 7 — Remaining providers, then the bake-off

- [ ] **7.1 AssemblyAI** — `disfluencies` switch; closest comparator to Deepgram.
- [ ] **7.2 Google STT v2** — hardest: GCS staging for long audio, and Chirp
      variants differ on whether diarization and adaptation coexist. Expect it to
      stress the registry.
- [ ] **7.3 ElevenLabs Scribe v2** — likely `glossary_mechanism: none`; the real
      test of whether lossiness reporting is honest.
- [ ] **7.4 Long-audio handling** — if any adapter chunks, it sets
      `chunked: true` and records boundaries.
- [ ] **7.5 Headline sweep** — glossary-off, all providers, all gold.
- [ ] **7.6 Glossary-on sweep.**
- [ ] **7.7 Selection report** — WER/DER **beside `baa_available` and
      `cost_usd`**. A provider that can't be deployed doesn't win.

---

## Run bookkeeping

The thing that keeps a sweep legible. Three ideas, and the mental model is just
the first line of each.

### One directory per run

`benchmarks/runs/<run_id>/` holds **everything** about that run: the resolved
config (every param actually sent, post-defaults), the normalized record, the
raw provider response, and the scores. Nothing about a run lives anywhere else.

`run_id` = `<UTC timestamp>_<engine>_<model>_<8-char config hash>`. The hash
guarantees uniqueness; the human-readable prefix means `ls` is a useful command
instead of a wall of hex.

### One row per run

`benchmarks/results/runs.csv` — append-only, git-tracked, one row per run,
pointing at its directory. The append-only-ness *is* the traceability, and being
a text file in git means you get the history free.

**Why not put runs in `manifest.sqlite`:** the manifest is gitignored because it
holds real names. Run results need to be tracked. Different lifecycles, different
files.

### One command that shows you both

`scripts/bench_status.py` — gold references and their state, runs grouped by
config with scores, and the empty cells of the matrix. **This is the actual
answer to "I could see quickly getting lost."** A ledger you have to reconstruct
by reading directories is one you'll stop trusting; a status command is one
you'll run.

### The silent failure this prevents

Comparing a WER from March against a WER from May when the normalizer changed in
April, or when gold was re-exported after a standards fix. Both numbers look
fine. Neither is wrong. The comparison is meaningless.

`normalizer_version` and **`gold_sha`** (the hash of the reference file actually
used) are provenance columns precisely so 6.2 can catch this mechanically. With
gold this scarce and this likely to be revised, `gold_sha` is not optional.

---

## Scarce gold

Realistically this is a handful of references, possibly fewer than five. That
changes two things.

**Excerpts count.** A reference does not have to be a whole encounter. A
carefully annotated **5-minute excerpt is a valid reference** as long as scoring
is restricted to that span — WER is a rate, not a total. For a fixed amount of
annotator time, **six 5-minute excerpts across varied conditions beat two full
20-minute encounters**: better strata coverage (standards §3), more cases, same
cost. This is the single highest-leverage response to a limited annotation
budget.

**With one annotator there is no noise floor.** Standards §10 assumes double
annotation to establish the floor below which no engine can meaningfully score.
One student means no floor. Options, in order of preference:

1. Second-annotate one excerpt yourself — one 5-minute excerpt is enough to get
   *an* estimate, and a rough floor beats none.
2. Report engine numbers with an explicit statement that no floor was
   established.

Do not quietly omit it. A benchmark without a noise floor can't distinguish a
good model from a saturated metric, and that limitation belongs next to the
numbers.

---

## Explicitly not in scope

Word-timing gold (CTM) · a streaming path · multi-provider production operation
(the end state is one vendor) · prosodic annotation · retrofitting transcripts
produced before the normalizer is versioned.

---

## Decisions this will surface

- **Disfluency-tuning parity.** If Whisper gets a hand-tuned prompt and Deepgram
  gets one flag, the comparison is rigged. Recommend recording a
  **`best_disfluency_config` per provider** in the registry, so the comparison is
  each provider at its own best effort, documented — not each provider at
  whatever effort it happened to receive.
- **Retry policy vs reproducibility.** A retried request returning different text
  breaks reproducibility. Recommend retrying only on transport errors, never on a
  successful-but-unexpected response, and recording the attempt count.
- **What `params_hash` covers.** Too narrow and the cache serves stale results
  across a config change; too broad and it never hits. Recommend every field sent
  to the vendor, nothing local.
- **Whether `medium` stays the local default** once cloud numbers exist. It was
  chosen for unattended batch throughput, not accuracy — and 0.3 may find that
  `large-v3` normalizes disfluencies away *more* aggressively, which would cut
  against it for this corpus.
- **Cost ceiling** for the sweep, set before it runs.
