# Current Status — read this first if you're picking this up cold

Tracked in git on purpose — unlike the root `HANDOFF.md` (deliberately
gitignored, a personal laptop→mini migration note from the very first
commit and now badly stale), this is meant to travel with a `git clone` and
stay current. Update it whenever a session ends with real state changed;
delete stale sections rather than letting them rot.

**Last updated:** 2026-09-04, folding in Kate's second gold case (261473,
scored 2026-09-02) on top of the Phase 1/2 merge (`b70a592`). All 178 tests
pass (`conda activate md-speech && python -m pytest -q`).

---

## What's actually built vs. what's real data

The ASR provider architecture (`docs/asr_provider_spec.md`) and its build
checklist (`docs/asr_provider_implementation_plan.md`, Phases 1–2) are done,
tested, and merged to `main`. Read the implementation plan for the detailed
phase-by-phase state — this section is the part that plan can't show: how
much of it has touched real data.

**Two real gold references exist**: cases `261456` and `261473`
(`benchmarks/references/<case>.gold.{txt,rttm}`, both recorded in the
manifest's `gold` table). Scores so far:

| Case | Engine | WER | CER |
|---|---|---|---|
| 261456 | `whisperx` | 0.41 | 0.35 |
| 261456 | `whisperx_disfluent` | 0.29 | 0.25 |
| 261473 | `whisperx_disfluent` | 0.41 | 0.32 |

**The "disfluency-preservation wins" read from the first case did not hold
on the second** — `whisperx_disfluent` scored 0.41 on 261473, roughly where
`whisperx` scored on 261456, not the 0.29 it got on its own first case.
`whisperx` hasn't been run against 261473 yet, so there's no same-case
comparison there — that's the obvious next run. This is exactly why the
first version of this note said "one data point, not a pattern": now it's
two points pointing in different directions, which is a *more* urgent reason
not to generalize, not a less urgent one. `whisperx_disfluent`'s config in
`providers.yaml` is still a first guess (`asr.DISFLUENCY_PROMPT` +
`condition_on_previous_text=True`), not a swept result — the actual sweep
(implementation plan Phase 0.3) has never been run, and this divergence is a
real argument for actually running it rather than trusting the one config
that happened to look good once.

Run `python scripts/bench_status.py` for the current live picture (gold
coverage, latest scores, empty matrix cells) rather than trusting this table
to stay accurate for long.

---

## Active thread: cloud provider research

Kate is hand-researching cloud providers directly in
`benchmarks/configs/providers.yaml` (not something to redo from scratch —
check there first). As of this writing:

- **Google**: researched and done (two distinct entries — plain STT and
  `google_medical_conversation`). Deprioritized as a deployment candidate:
  GCS staging for long audio is more complexity than it's worth right now.
- **AssemblyAI**: in progress, and the recommended next cloud provider —
  its `disfluencies` param is a real preservation switch (the same property
  that made `whisperx_disfluent` win), and it's structurally the closest
  comparator to Deepgram (diarization + word timing + a real disfluency
  switch), so it stresses the adapter pattern most cheaply if built next.
- **Deepgram**: free credits available, but not a vendor Kate's research
  partners care about — bank the credits for a later cross-check rather than
  leading with it.
- **ElevenLabs**: least-specified row; likely no glossary mechanism at all
  and "Scribe v2" itself is unconfirmed against public docs.

**Architecture decision, settled**: cloud adapters call vendors via
`requests` directly, not vendor SDKs. Reasoning: the raw `response.json()`
*is* the artifact `asr_provider_spec.md` §4 wants cached and compared, with
no SDK object serialization step in between (traceability); this is
benchmark-scale usage (a handful of gold cases), not production-scale, so an
SDK's retry/pooling machinery isn't worth 3–4 more heavy dependencies
(simplicity); and four raw JSON payloads built from the same
`providers.yaml`-driven pattern are easier to reason about side by side than
four different SDK object shapes (consistency). Revisit per-vendor only if
raw HTTP turns out genuinely painful for one of them (Google was the likeliest
candidate for that — moot while deprioritized).

---

## The one blocking question, not yet resolved

**Whether IRB/consent actually covers sending learner/preceptor voice to a
third-party vendor.** `cases.cloud_release` defaults to 0 and enforces this
at the code level (no adapter can send audio for a case without it), but
that's a technical gate, not a policy answer. Confirm the real-world status
*before* spending real effort on a cloud adapter or vendor account billing,
not after — this is cheap to check now and expensive to discover late.

---

## Deliberately not built — don't rebuild these from scratch

Each is flagged inline in the implementation plan with its reasoning; listed
here so a fresh read doesn't mistake "missing" for "forgotten":

- The formal adapter-contract abstraction (Phase 3.1) — today's engines use
  a sibling-function pattern instead; revisit once a cloud adapter needs
  shape enforcement four ad hoc functions can't informally provide.
- A glossary renderer that turns `glossary.yaml` into a real Whisper
  `initial_prompt` (Phase 3.4) — the loader exists, the renderer doesn't.
- DER scoring against `.gold.rttm` (Phase 6.2) — `run_benchmark.py` still
  only computes WER/CER.
- Number canonicalization in `normalize.py` — deferred until real gold has
  numeric content to design rules against, rather than guessed blind.
- `.eaf`-level excerpt filtering (Phase 5.2) — `--span-start`/`--span-end`
  records and *scores* against a span, but doesn't slice a fully-annotated
  `.eaf` down to a sub-window at export time. Fine as long as annotators
  only segment the excerpt's window in the first place.

---

## Housekeeping

Three feature branches are fully merged into `main` and safe to delete
whenever someone wants to (`asr-provider-config`,
`benchmark-bookkeeping`, `annotation-standards-and-asr-providers`) — left
alone here rather than deleted unprompted. `speaker-sex-detection` predates
this work; unknown state, not touched.

Kate's own in-progress edits get left uncommitted when a session ends mid-
file — check `git status` before assuming the working tree is clean, and
never sweep her edits into a commit without asking.
