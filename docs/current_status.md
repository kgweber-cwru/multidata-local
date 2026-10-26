# Current Status — read this first if you're picking this up cold

Tracked in git on purpose — unlike the root `HANDOFF.md` (deliberately
gitignored, a personal laptop→mini migration note from the very first
commit and now badly stale), this is meant to travel with a `git clone` and
stay current. Update it whenever a session ends with real state changed;
delete stale sections rather than letting them rot.

**Last updated:** 2026-09-23 — **the cloud annotation environment was torn
down: audio was not robust on frequent, repeated replays, which is
disqualifying for a task that's entirely about listening. Read "Annotation
environment abandoned" below before touching `annotation/` or restarting
that effort.**
Pose is still parked — see [pose_parked.md](pose_parked.md) before touching
anything pose-related. Earlier: 2026-09-14 pose parked in favor of
transcription validation; 2026-09-10 IRB cleared third-party vendor inference
(unblocking Phase 4); 2026-09-04 folded in Kate's second gold case (261473) on
top of the Phase 1/2 merge (`b70a592`). All 205 tests pass
(`conda activate md-speech && python -m pytest -q`) — up from 178, from the
annotation package's surviving unit tests.

---

## Priorities, as of 2026-09-14

**One thing is being worked on: getting audio transcription validation going.**
Everything below either serves that or is explicitly parked.

**Active:**

1. **Getting a second annotator producing gold, somehow.** The cloud
   remote-desktop attempt at this is dead — see "Annotation environment
   abandoned" below, which is also where to start if you're picking this back
   up. `annotation/make_kit.py`, `submit_checks.py`, and `pull_submission.py`
   survived and are still useful regardless of what replaces the delivery
   mechanism. **No delivery mechanism is currently chosen.**
2. **More gold references.** The first campaign is 5–10 hours of video, to be
   selected by the people who need the gold. Two references exist today.
3. **The first cloud ASR adapter**, now that IRB has cleared vendor inference —
   but build the `cloud_release` interlock first (see below).

**Parked, deliberately:**

- **Pose.** Generation works and is finished for the current sample; usability is
  barely started. **[pose_parked.md](pose_parked.md)** records where it got to,
  the one gap that blocks everything downstream (pose output has no person
  identity), and what to do first on return. Read that rather than reconstructing
  it.
- **Postgres for the manifest.** Agreed as the right destination, deliberately
  *not* next. It solves the two-machine fork problem — `manifest.sqlite` forking
  when both boxes write `pose_status` — and that problem is parked with pose.
  Near-term transcription work is single-machine on the mini, where a file-based
  database has nothing to reconcile. Meanwhile the cost is real: a driver in all
  three conda envs including the deliberately-minimal `md-pose`
  ([manifest.py:21](../src/multidata/manifest.py#L21) says "stdlib `sqlite3` on
  purpose"), a server that must be up before anything runs, and a test suite that
  currently builds a throwaway database file per test and would need either a
  live server or two SQL dialects maintained forever.
  **What keeps it cheap to do later:** all database code already lives inside
  `manifest.py`, and nothing else in the repo imports `sqlite3`. Keep it that way
  and the move stays a contained job. When it happens, it goes in a container on
  tofino, with nightly dumps to somewhere that isn't tofino.
- **One shared copy of `data/`** on the private network's 1 TB disk. Same
  reasoning: it's the fix for two machines drifting, and that's the parked
  pipeline. Annotation doesn't need it — a case kit is ~230 MB.
- **The production pipeline** that sends everything somewhere for long
  processing (probably tofino). Aware of it, not designing it yet.

**Settled, don't re-litigate:**

- **Where the data lives.** Irreplaceable bytes (raw video, gold `.eaf`) belong
  in one authoritative place with a backup; regenerable bytes (pose, wav,
  transcripts) are caches and should not be synced between machines. The mini has
  313 GB free and the full corpus projects to ~1.5 TB as things stand, so the mini
  is not the long-term home — but with pose parked and the near-term job at 5–10
  hours of video (~4 GB), that deadline is not close.
- **The network shape.** The mini and tofino sit on a private network that can
  reach out but cannot be reached in, and annotators at home or on campus cannot
  see it either way. Still true, still relevant to whatever comes next — it's
  what made a cloud-hosted delivery mechanism the obvious choice in the first
  place, even though the cloud desktop implementation of that idea didn't work
  out. Annotators never need the manifest, whatever the mechanism turns out to
  be — a kit carries everything they need as flat files.

---

## Annotation environment abandoned (2026-09-23)

**Status: torn down, not paused.** All the code and design docs for a
cloud-hosted remote desktop are deleted. If you're reading this cold, this
section is the whole story — you shouldn't need to dig through commit
history to reconstruct it, though the history (29 commits on the `labeling`
branch, `2da900a..760578d`) is there if you want the blow-by-blow of any
individual bug.

### What the idea was

Give each annotator a cloud VM with ELAN pre-installed and a case pre-linked,
reached over a remote desktop protocol, so gold transcription could happen on
more than one person's machine without encounter media ever landing on
anyone's laptop. Design docs were `annotation_environment_design.md` (the
environment) and `annotator_image_design.md` (the machine image) — both
deleted along with the code; their substance, where it's still true, is
folded into this note and into "The blocking question" section below.

### What got built, and what of it survives

`annotation/make_kit.py`, `annotation/submit_checks.py`, and
`annotation/pull_submission.py` are **still here and still work** — see
[../annotation/README.md](../annotation/README.md). They were tested against
real data (a 57 MB kit built from case 261473 in 18 s with canonical,
pre-linked media URLs; `submit_checks.py` correctly caught two empty segments
in real gold) and don't depend on anything cloud-specific except an optional
GCS path in `pull_submission.py` that still works if you have a bucket and is
ignored if you don't (`--from-dir` instead). **Nothing else survived** —
`annotation/gcp/` (all the provisioning and connection scripts) and
`annotation/image/` (the VM image build) are gone, along with
`annotation/config.sh`.

### What actually happened, briefly, so it isn't repeated

Roughly two weeks of iteration, most of it fighting the delivery mechanism
rather than the actual annotation problem:

- **VNC first.** Got a machine building and connecting, then discovered VNC's
  protocol carries no audio at all — a blocking problem for a task that is
  entirely about listening (`uh-huh` vs `uh-uh` is a glottal catch vs. an
  audible /h/, and the whole point of the exercise is not guessing at that).
- **Switched to RDP** (`xrdp` + PipeWire, since `pulseaudio-module-xrdp`
  doesn't exist as a Debian package at all — only the PipeWire audio module
  does, and only in `bookworm-backports`). Audio started working.
- **Then a real, specific bug**: ELAN keeps a *per-medium* volume, and the
  `.pfsx` files this project had been generating left the `.wav` at volume 0
  while the `.mp4` was at 100 — so ELAN's video played and its audio didn't,
  which looked exactly like a channel/driver problem and wasn't one. Fixed by
  hand on the last working machine (never ported into `make_kit.py` — if this
  effort restarts, that fix has to happen in the kit generator, not by an
  annotator hand-editing a settings file).
- After the volume bug was fixed, audio was tested for real — the actual
  criterion, looping a segment repeatedly the way an annotator works — and
  **failed it: not robust on frequent, repeated replays.** So this is a
  measured negative result, not an accumulated-fragility judgment call. It's
  the reason this got torn out rather than patched further: the whole point
  of the environment is that annotators loop hard passages many times, and
  an audio path that degrades under exactly that usage pattern is
  disqualifying, whatever the cause turns out to be (PipeWire/xrdp buffering,
  the IAP tunnel, RDP's audio compression — not isolated, and not worth
  isolating for a mechanism that's being abandoned anyway).
- Underneath the audio saga, a long list of infrastructure bugs got fixed
  along the way (env vars not surviving `sudo`/`ssh`, `/etc/skel` populated
  in the wrong order, guessed paths instead of asked-for ones, non-idempotent
  install scripts, IAP not terminating where a comment confidently claimed it
  did). None of that is wasted knowledge in an abstract sense, but none of it
  is *recoverable* either — the code it lived in is deleted. If a cloud VM
  approach is tried again, expect to rediscover most of it.

### What to actually decide, if this comes back

Two candidate mechanisms are on the table, **not yet decided between**
(2026-09-23):

1. **A dedicated annotator laptop.** Project-owned, so it satisfies the
   original requirement (no encounter media on a *personal* machine — a
   project-owned laptop isn't one) and sidesteps every problem above: ELAN
   runs natively, audio is real local audio, keystroke timing has no network
   latency to fight. The tradeoff is physical logistics — procurement,
   encryption, shipping/handoff, physical loss/theft exposure — in place of
   cloud logistics.
2. **CWRU-managed machines annotators already have, with kits distributed
   through Box** (or another CWRU-approved secure storage) instead of a
   purpose-built delivery pipeline. Media and the `.eaf` get uploaded to a
   Box folder per assignment; the annotator downloads and opens locally. This
   also sidesteps the audio problem (native playback again) without owning
   physical hardware, but reopens the piece of the original brief this whole
   effort was built to avoid — "don't want to configure ELAN for everyone."
   With N different annotator machines instead of one image, that becomes N
   individual installs/configurations rather than one done once. Also
   unconfirmed: whether Box is on CWRU's BAA-covered-services list the same
   way enterprise Google is — check before relying on it, same governance
   question that mattered for the cloud approach (see below).

Whichever is picked, the ELAN-configuration knowledge from the deleted image
build isn't lost, just not in the working tree: `~/.elan_data/elan.pfsx`'s
confirmed format and keys (`AutomaticBackupOn`, `BackUpDelay`) are in
`git show b3aafcb` on this branch. The per-medium volume bug above is the one
piece of that knowledge that matters regardless of mechanism — any kit
generator for any delivery path needs to set volume on *every* linked medium,
not just the one an annotator happens to notice.

Whichever mechanism is chosen, most of the kit-building work carries over
unchanged (see the table in `annotation/README.md`) — the only design
decision baked into the surviving code that's specific to the old approach is
`CANONICAL_ROOT` in `make_kit.py` (`/srv/multidata/case`), the fixed path
every kit assumes the annotator's machine mounts a case at. That pattern —
same fixed path on every machine, so ELAN never shows a "locate media"
dialog — is sound for a laptop too; the specific path just needs deciding
again.

The governance facts (IRB clearance for vendor inference, BAA-covered
enterprise Google for data at rest) are untouched by any of this — see "The
blocking question" section below. They don't depend on which delivery
mechanism wins.

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
  instance under BAA**. This is a governance fact, independent of the failed
  remote-desktop *implementation* below — it stays true whatever delivery
  mechanism gets picked next. One thing worth remembering from the now-deleted
  design doc: **a BAA covers a named service list, not "Google" generically** —
  Compute Engine, Cloud Storage, and IAM were confirmed covered; Chrome Remote
  Desktop was flagged as probably not, which is part of why the design used
  RDP/VNC-on-a-VM instead of CRD in the first place.
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

**`labeling`** is the current branch, 29 commits ahead of `main`
(`main..labeling`) and not merged. It holds this file, the surviving
`annotation/` tooling, IRB/BAA governance updates, and — in earlier commits —
the full history of the abandoned cloud environment before it was torn back
out. Not merged yet; nothing here says it's ready to be.

Kate's own in-progress edits get left uncommitted when a session ends mid-
file — check `git status` before assuming the working tree is clean, and
never sweep her edits into a commit without asking.
