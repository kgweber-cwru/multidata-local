# Current Status — read this first if you're picking this up cold

Tracked in git on purpose — unlike the root `HANDOFF.md` (deliberately
gitignored, a personal laptop→mini migration note from the very first
commit and now badly stale), this is meant to travel with a `git clone` and
stay current. Update it whenever a session ends with real state changed;
delete stale sections rather than letting them rot.

**Last updated:** 2026-09-10 — IRB cleared third-party vendor inference
(see below), which unblocks Phase 4. Previously 2026-09-04, folding in Kate's
second gold case (261473, scored 2026-09-02) on top of the Phase 1/2 merge
(`b70a592`). All 178 tests pass (`conda activate md-speech && python -m pytest -q`).

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
  `google_medical_conversation`). ~~Deprioritized as a deployment candidate:
  GCS staging for long audio is more complexity than it's worth right now.~~
  **The reason for that deprioritization has expired** — see below.
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

**Google's deprioritization is worth revisiting (2026-09-10).** It rested on
one stated cost — "GCS staging for long audio is more complexity than it's worth"
— and two facts just removed it: the project is standing up a GCS bucket in the
enterprise org anyway (annotation environment, §4.1), so the staging path stops
being extra work and becomes reuse; and Google is *inside the existing BAA*,
where AssemblyAI, Deepgram and ElevenLabs would each need their own agreement
plus a per-request no-train flag before any audio can go to them (0.6). That's a
material asymmetry in time-to-first-real-result.

This does **not** overturn the AssemblyAI recommendation on the research merits
— its `disfluencies` switch is still the closest thing to a direct test of the
property that made `whisperx_disfluent` win, and that's the actual open
scientific question. It does mean the *ordering* argument has flipped: Google is
now the cheapest vendor to get a real number from, and AssemblyAI is the most
informative one. Pick deliberately rather than inheriting the old ranking; if
the goal is to exercise the adapter pattern end to end against real audio
without waiting on paperwork, Google is now the path of least resistance.

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

## The blocking question, now resolved — and what it exposed

**IRB cleared inference with online vendors (2026-09-10).** Sending
learner/preceptor voice to a third-party ASR vendor is approved. This was the
one item gating Phase 4, and it is no longer gating anything: implementation
plan **0.5 is done**.

What it does *not* cover, and don't let the good news blur these:

- **Storing the corpus in a third-party cloud** is a different question from
  running inference against a vendor API. That one is *also* now settled, but
  separately: the corpus lives in the institution's **enterprise Google
  instance under BAA**
  ([annotation_environment_design.md](annotation_environment_design.md) §0).
  Note a BAA covers a named service list, not "Google" — see that doc's §4.5
  for the one component that trips on this.
- **Per-request no-train / zero-retention flags are still requirements**, not
  preferences (`asr_provider_spec.md` §9). Vendor defaults frequently permit
  retention, so IRB approval plus a default-configured request is still a
  leak. This is per-vendor account configuration *and* per-request parameters —
  plan item 0.6, still open.

### The interlock does not actually exist yet

Worth stating plainly because the previous version of this note got it wrong:
it claimed `cloud_release` "enforces this at the code level (no adapter can
send audio for a case without it)." **No code anywhere reads
`cases.cloud_release`.** The column exists (`manifest.py`, `models.py`) and the
intent is documented (`asr_provider_spec.md` §9), but the check is
implementation plan item **4.2, unchecked**. The claim was vacuously true only
because no cloud adapter exists to be gated.

That distinction stopped being academic the moment IRB cleared vendor
inference, because the first cloud adapter is now the active work. The order
that matters:

1. **4.2 before 4.3.** Build the interlock before the adapter that would be
   subject to it, and verify it deliberately — point it at a `cloud_release=0`
   case and confirm it refuses *before any audio is read*. A gate that fails
   open is worse than no gate, because it gets trusted.
2. **Then 0.7** — set `cloud_release=1` with a recorded
   `cloud_release_basis` on the dev subset. Right now **0 of 1307 cases** have
   it set, which is the correct default and also means every cloud run will
   refuse until someone makes a deliberate, auditable decision. Record the IRB
   determination reference in the basis, not just `simulated_patient`.

Related and currently unenforced: **`consent_ref` is empty on all 1307 cases**,
while the pipeline doc says to "refuse to process rows without it." Not
urgent for local stages, but if `cloud_release_basis` is going to cite consent,
the field it cites should have something in it.

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
