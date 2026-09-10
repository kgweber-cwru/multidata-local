# Annotation Environment — design

A self-contained, GCP-hosted virtual desktop that hands an annotator a ready-to-
work ELAN session: correct template, correct tiers, media already linked, nothing
to install, nothing to configure, and no encounter media on a personal machine.

**Status: design only.** Nothing here is built. Read
[current_status.md](current_status.md) for what *is* built.

Companion docs: [annotator_guide.md](annotator_guide.md) is the workflow this
environment has to serve; [transcription_standards.md](transcription_standards.md)
(cited **[S§n]**) is the policy it has to enforce. Where this design and the
standards disagree, the standards win.

---

## 0. Governance position

Settled, in two steps:

- **2026-09-10 — IRB cleared inference with online vendors.** Sending a case's
  audio to a third-party ASR API is approved.
- **Data residency — resolved.** The corpus lives in the institution's
  **enterprise Google instance, under BAA**. That covers the thing this design
  needs and the earlier draft flagged as unestablished: identifiable audio,
  video, and Layer 1 `.eaf` files at rest on Google infrastructure, with
  annotators accessing them interactively.

Today's corpus has no patient PHI (the patient is an actor), but learners and
preceptors are real people who self-identify verbally, so it is identifiable
human-subjects data regardless. A BAA is more coverage than that strictly needs
— and it is exactly the coverage the pipeline doc's "day real clinical data
lands" scenario will need, which is the same reasoning that put
`cases.cloud_release` in the schema before anything could use it.

Three consequences that are easy to miss, and one of them changes a
recommendation:

1. **A BAA covers a named list of services, not "Google".** Every component
   here must be on the covered-services list for the agreement. Compute Engine,
   Cloud Storage, and IAM are; **Chrome Remote Desktop is not a Google Cloud
   product** and should be assumed off-list until checked. That inverts the
   access-layer recommendation — see §4.5.
2. **Not Shared Drive.** "All the data is in enterprise Google" invites putting
   media in a Shared Drive, and Drive is BAA-coverable. It's still wrong here:
   ELAN needs a filesystem path it can seek in, and Drive offers neither. GCS
   plus §4.2's canonical mount path is the version that works. Drive is a fine
   home for the *guide and standards*, not for media.
3. **Audit logging becomes load-bearing**, not hygiene — §5.

**Portability, kept anyway.** The design still uses only Compute Engine, GCS,
and IAM, and the parts carrying the real value — the machine image, the golden
ELAN preferences, the case-kit format, the submit path — work unchanged on a
university-managed VMware/Proxmox host or a single on-prem Linux box. Cheap to
preserve, and it means an access-layer or hosting change is a §4.5 change rather
than a redesign.

---

## 1. What this has to do

Requirements, derived from the existing docs rather than invented here:

| # | Requirement | Source |
|---|---|---|
| R1 | Layer 1 `.eaf` files and encounter media never sit on a personal machine | [S§8] handling caution, guide §7 |
| R2 | Annotator does no ELAN installation or configuration | this task |
| R3 | ELAN opens with **both** media files linked — `.mp4` and `.wav` — with no "locate media" dialog | guide §1 |
| R4 | The six template tiers, from the tracked `elan/template.etf`, provably identical for every annotator | [S§4], `elan/README.md` |
| R5 | Pass 1 is **blind** — an annotator can't see a machine draft or another annotator's file | [S§9], [S§10] |
| R6 | Work survives disconnect and days of elapsed time (6–10× realtime, ~a day per case) | guide preamble |
| R7 | Finished `.eaf` gets back to managed storage and into `eaf_to_gold.py` without an email or a USB stick | [S§8], [S§11] |
| R8 | Boundary-placement timing stays inside the ~100 ms tolerance | [S§5] |
| R9 | Easy enough to deploy and operate by one person, part-time | this task |

R5 is the one most easily lost. Blindness is currently an honor-system property
of the workflow; the design below makes it an **access-control** property, which
is the only version of it that survives 20% double-annotation ([S§10]) with two
annotators who know each other.

**Not on the table: replacing ELAN.** The standards, the tier scheme, the
segmentation/transcription two-mode workflow, and `eaf_to_gold.py` are all
ELAN-shaped. Swapping in a web annotation tool would invalidate the standards
document, not simplify the deployment.

---

## 2. Architecture at a glance

```
  ADMIN SIDE (Mac mini — unchanged, single writer)
  ┌───────────────────────────────────────────────────┐
  │ manifest.sqlite   data/raw/   data/audio/         │
  │ elan/template.etf                                 │
  │                                                   │
  │  make_kit.py  ─────────────┐   ingest_submissions.py
  └────────────────────────────┼──────────────▲───────┘
                               │              │
                        ┌──────▼──────────────┴──────┐
                        │  GCS bucket (regional)     │
                        │  kits/<case>/<annotator>/  │  ← read by one VM only
                        │  work/<case>/<annotator>/  │  ← written by one VM only
                        └──────▲──────────────┬──────┘
                               │ stage at boot│ rsync every 5 min
                        ┌──────┴──────────────▼──────┐
   annotator's browser  │  Annotator VM (ephemeral)  │
   ──── remote desktop ─▶  Debian + Xfce + ELAN 7.x  │
   (no data leaves it)  │  /srv/multidata/…          │  ← canonical mount path
                        └────────────────────────────┘
```

One VM per annotator, created for an assignment and deleted after the submission
is ingested. The VM is cattle; the bucket and the mini hold state.

---

## 3. Why one VM per annotator

A single shared multi-user annotation host is the obvious-looking alternative and
it is worse on every axis that matters here:

- **R5 falls out for free.** A VM only ever has one annotator's assigned case
  staged on it, and its service account can only read that case's kit prefix. On
  a shared host, "don't open the other annotator's folder" is a request.
- **Cost control is per-annotator.** Annotators work in bursts of a few hours.
  A stopped VM costs disk only. A shared host is always on.
- **Teardown is a real erasure.** Delete the VM and its disk and the Layer 1
  copy is gone. On a shared host, cleanup is a script you have to trust.
- **Blast radius.** A misconfigured shared host exposes the whole corpus.

The cost is N images to keep in sync, which is why §4.4 makes the image a build
artifact with a version tag rather than a hand-tended pet.

---

## 4. Components

### 4.1 Storage: GCS as system of record, staged to local disk

The bucket holds kits and submissions. The VM works against **local persistent
disk**, staged at boot.

Rejected alternatives, so nobody re-litigates them:

| Option | Why not |
|---|---|
| `gcsfuse` mount of the media | ELAN scrubs and loops constantly. Random-access seeking through a FUSE layer is exactly gcsfuse's weak case; even with file caching it's a UX gamble on the single most latency-sensitive part of the work. |
| Filestore (managed NFS) | Right performance profile, but 1 TiB minimum (~$200/mo) to serve a handful of cases, and it re-creates the shared-visibility problem §3 just solved. |
| Local disk, staged from GCS | **Chosen.** Native seek performance, least privilege, costs pennies. Staging a case is a one-time ~1–3 GB in-region copy — under a minute. |

Bucket configuration: uniform bucket-level access (no ACLs), object versioning
on (a `rsync` that races an ELAN save must be recoverable), and a lifecycle rule
that expires `kits/` after the assignment window. Same region as the VMs.
**Skip CMEK** unless the institution specifically asks: Google's default
at-rest encryption satisfies the BAA, and customer-managed keys add a key
rotation and availability dependency that buys nothing here.

### 4.2 The canonical mount path — the media-linking linchpin

This is the whole answer to R3, and it's a one-line decision with a large payoff.

An `.eaf` records media twice, in `MEDIA_URL` (absolute) and
`RELATIVE_MEDIA_URL`. Today's gold files carry a path from the machine they were
made on:

```xml
<MEDIA_DESCRIPTOR
    MEDIA_URL="file:///Users/kate/projects/multidata-local/data/audio/261456/16.wav"
    MIME_TYPE="audio/x-wav" RELATIVE_MEDIA_URL="../16.wav"/>
```

On any other machine that absolute URL is dead, and ELAN falls back to the
relative one — which in that example is also wrong (the file is at
`../../audio/261456/16.wav`; this one already only resolves by luck of where it
was opened from). What the annotator sees is the "locate media" dialog. That
dialog is precisely the ELAN-fiddling this project is trying to stop doing.

**Fix: every VM mounts the case at the same absolute path, and the kit generator
writes that path into `MEDIA_URL`.**

```
/srv/multidata/case/<case_id>/
├── media/
│   ├── <case_id>.mp4          # camera per manifest videos.camera
│   └── <case_id>.wav          # camera per cases.audio_camera
└── <case_id>.pass1.eaf        # pre-linked, six empty tiers
```

With `MEDIA_URL="file:///srv/multidata/case/261456/media/261456.wav"` and
`RELATIVE_MEDIA_URL="media/261456.wav"`, **both** attributes resolve on **every**
VM. Not one annotator ever sees the dialog, and a kit stays valid if it's
re-staged to a different VM later.

Note this path is *not* the repo's `data/` layout. It doesn't need to be — the
`.eaf` that comes back is re-homed to `data/gold/<case_id>/` by the ingest step
(§4.6), and `eaf_to_gold.py` reads annotations and timings, not media URLs.

### 4.3 The case kit

A kit is a self-contained assignment. `make_kit.py` runs **admin-side** (it needs
the manifest and the raw media, both of which stay on the mini), takes a
`--case` and an `--annotator`, and produces:

| File | Contents |
|---|---|
| `<case_id>.pass1.eaf` | `elan/template.etf`'s six tiers, plus two pre-wired `MEDIA_DESCRIPTOR`s per §4.2. Empty of annotations. |
| `media/<case_id>.wav` | The `cases.audio_camera` wav from `data/audio/<case_id>/` |
| `media/<case_id>.mp4` | Video for speaker identification — see the proxy note below |
| `kit.json` | `case_id`, camera ids, `span_start`/`span_end` if this is an excerpt, `standards_version`, `annotator`, image tag, source file sha256s |
| `README.txt` | Two paragraphs: which case, which window, where the guide is |

`pympi`'s `add_linked_file` already writes media descriptors —
[elan.py:87-89](../src/multidata/elan.py#L87-L89) does it for the machine draft.
The kit generator is a thin sibling of code that exists, aimed at the template
instead of a transcript, not new machinery.

**Excerpt window.** [S§3] allows excerpt references and `run_benchmark.py`
already scores against a span, but `.eaf`-level span slicing is deliberately
unbuilt — which holds only "as long as annotators only segment the excerpt's
window in the first place" (current_status.md). So a kit for an excerpt case
should pre-create a single `NOTES` annotation spanning the window, labelled
`ANNOTATE ONLY THIS WINDOW`. That makes the constraint visible on the timeline
rather than in a README nobody re-reads, and it cannot contaminate Layer 2:
`NOTES` is excluded from both `gold.txt` and `gold.rttm`
([elan.py:133](../src/multidata/elan.py#L133)).

**Proxy video.** The workflow is overwhelmingly audio: segmentation and
transcription both work off the waveform, and video's job is answering "who is
talking". A 480p H.264 proxy is enough for that, and it cuts staging size,
decode cost, and the remote-desktop bandwidth of a video-scrub. Default to the
proxy; keep a `--full-video` flag for a case where identification is genuinely
hard. Transcoding also sidesteps codec surprises in ELAN's Linux media stack
(§8), which is a second reason to prefer it.

**Naming, incidentally fixed.** The guide says save as `<case_id>.pass1.eaf`;
what's actually on disk today is a mix of `pass1.eaf`, `261456.eaf`, and
`261473.eaf`. When the kit ships the file pre-named, the annotator never names
one, and the drift stops.

### 4.4 The image — where "no configuring ELAN" actually lives

One custom Compute Engine image, versioned (`annotator-v1`, `-v2`, …), built by a
script and never hand-edited in place. Contents:

- Debian 12, Xfce4 (light, predictable, no compositing to fight the remote
  desktop), fonts, `vlc` + `libavcodec-extra` for ELAN's media backend.
- **ELAN 7.x, pinned**, from the Linux distribution (it ships its own JRE, so
  there's no separate Java to manage). The version goes in the image tag and
  into `kit.json` — an ELAN upgrade is a change-control event under [S§12] the
  same way a model version is, and unpinned upgrades across a corpus are exactly
  the drift the pipeline doc warns about.
- **A golden ELAN preferences directory, baked into `/etc/skel`** so every new
  session starts from it. This is the payload of R2. It should pre-set:
  - segmentation-mode keystroke config and **delayed mode**, calibrated per §8 —
    the guide currently tells annotators to go experiment with 200 ms, which is
    a per-person setting that should be a per-deployment one;
  - **autosave on, short interval** — the guide has to nag "save often" today;
    that's a human failure mode we can simply delete;
  - tier colours/fonts, waveform display prefs, default template and media
    directories pointing at `/srv/multidata/`.

  Verify the exact preferences path and file set while building the image (`~/.elan_data`
  on Linux, as of ELAN 6/7) and record what was set and why alongside the build
  script — a golden prefs blob nobody can read is a liability.
- **The guide on the desktop.** The guide assumes the cheat sheet is printed or
  on a second screen; on a single remote desktop it should be a window. Ship the
  guide and standards as local HTML with a desktop launcher.
- Desktop launchers: **Start annotating** (opens the staged `.eaf` in ELAN),
  **Submit finished pass**, **Guide & cheat sheet**. Nothing else — no browser,
  no file manager pointed anywhere interesting, no terminal for a non-admin user.

### 4.5 Access layer

The only genuinely open judgement call, and the only piece the rest of the design
doesn't depend on — but the BAA narrows it. **Recommendation: (b).**

**(b) IAP + browser VNC — recommended.** Xfce + KasmVNC on the VM, behind an
HTTPS load balancer with Identity-Aware Proxy. The annotator opens a URL, signs
in with their institutional Google account, and IAM decides whether they get a
desktop.

- **Every component is a Google Cloud service** under the agreement — Compute
  Engine, Cloud Load Balancing, IAP, GCS, IAM. Nothing about the session leaves
  the covered perimeter.
- **Nothing to install, and no per-account setup.** A URL and the account they
  already have. This is now the *lower*-friction option for the annotator, not
  the higher one.
- **Every session is an IAM event against a named principal** — who connected
  to which case VM, when. Under a BAA that's not a nice-to-have; it's the
  access-log story (§5).
- **Clipboard and file transfer are yours to configure** directly in the VNC
  server, rather than via a client-side Chrome policy you're trusting to apply.
  Turn both off: "off personal machines" (R1) has to include "can't be dragged
  onto one".
- The cost: a load balancer, a managed certificate, an OAuth brand, and
  host-based routing to per-annotator backends. This is real Terraform, roughly
  a day of setup, and about $18/month standing (§9).

**(a) Chrome Remote Desktop — the fast path, probably now closed.** Zero
networking: the startup script installs the CRD host, the annotator connects at
`remotedesktop.google.com`, and there's no load balancer, certificate, or public
IP anywhere. It was the original recommendation for exactly that reason.

The problem is scope. A remote-desktop session carries the pixels of
identifiable video and the keystrokes of a transcript containing real names, and
it routes them through Google's CRD relay — **a Chrome service, not a Google
Cloud one, and so almost certainly not on the Cloud BAA's covered-services
list.** Access control isn't the issue; the annotators are on institutional
accounts either way. The issue is whether that traffic is inside the agreement.

> **Verify this against the actual agreement before dismissing it** — the
> covered-services list is a published document and the institution's contract
> may differ. If CRD *is* covered, (a) is a legitimate way to skip a day of
> Terraform and $18/month, and the rest of this design is unchanged. Treat "not
> covered" as the working assumption, because that's the direction where guessing
> wrong is expensive.

Only the launchers and the startup script differ between (a) and (b). Storage,
kits, the image, and the submit path are identical, so this is a reversible
decision — which is the reason to record it as a decision rather than a default.

### 4.6 Submit path, and the single-writer rule

**Continuous:** a systemd timer runs `gcloud storage rsync` from
`/srv/multidata/case/<case_id>/` to `gs://…/work/<case>/<annotator>/` every five
minutes. This is crash and idle-shutdown insurance, not the handoff — with object
versioning on, a mid-save race is recoverable.

**Explicit:** the **Submit finished pass** launcher does a final sync, writes
`submission.json` (annotator, timestamps, image tag, `kit.json` echoed back), and
marks the prefix complete. It is also the natural place to enforce the guide's
step 7 — refuse to submit while any segment is empty, since empty segments become
phantom turns in the RTTM and the check is mechanical.

**Admin-side:** `ingest_submissions.py` pulls completed submissions to
`data/gold/<case_id>/<case_id>.pass1.eaf` and runs `eaf_to_gold.py`, which
already redacts from the manifest and writes the `gold` table row.

**The rule that keeps this simple: VMs never touch `manifest.sqlite`.** The
manifest stays single-writer on the mini. Kits carry the manifest facts an
annotator needs as flat `kit.json`; submissions carry facts back the same way.
A shared sqlite over object storage, or a real database, is the obvious next step
and it is not worth taking — the state here is a handful of assignments, and
`gold.annotator` / `gold.pass1_frozen_at` / `gold.span_*` already exist to record
the outcome. Note that [S§9] freezes pass 1 on completion: after ingest, the
right move is to delete the VM, which makes "frozen" a physical fact rather than
a policy.

---

## 5. How access control enforces the standards

| Standard | Enforcement |
|---|---|
| [S§9] blind pass 1 | The VM's service account can read `kits/<case>/<annotator>/` and write `work/<case>/<annotator>/`. It cannot list the bucket, cannot read another annotator's prefix, and no machine draft is ever staged. |
| [S§10] independent double annotation | Two annotators on the same case get two kits, two prefixes, two VMs. Neither can reach the other's, so the second pass is blind by construction, not by agreement. |
| [S§8] Layer 1 stays on managed storage | No public IP; egress via Cloud NAT only (or none at all, if the image needs no runtime internet). No file transfer, no clipboard (§4.5). No browser on the desktop. Data path is bucket ↔ VM, both inside the project. |
| [S§8] Layer 2 is derived, never hand-edited | Unchanged: `eaf_to_gold.py` runs admin-side, off the submitted `.eaf`. |
| [S§4] identical tier set | Kits are generated from the tracked `elan/template.etf` by one script. An annotator cannot start from anything else, because they never create a file. |
| BAA — demonstrable access records | **Enable Cloud Audit Logs Data Access logging on the bucket.** It is off by default for GCS, which means the record of who read which case's media does not exist unless someone turns it on. Combined with IAP's per-session events (§4.5b), "who accessed case 261456, when" becomes answerable rather than inferred. Turn it on when the bucket is created, not after the first question about it. |

VPC Service Controls would draw a perimeter around the bucket so a stolen
credential couldn't pull data out of the org at all. It's org-level configuration
with org-level blast radius, so it's IT's to apply rather than yours to
self-serve — but in an enterprise org already operating under BAA, a perimeter
around the project is a normal request rather than an exotic one, and it's worth
asking for while the project is being created instead of retrofitting.

---

## 6. Annotator experience, end to end

1. Email: "case 261456 is ready — here's your link."
2. Open the link in a browser, sign in with the institutional account. An Xfce
   desktop appears with three icons.
3. Click **Start annotating**. ELAN opens on `261456.pass1.eaf`: six empty tiers,
   waveform loaded, video loaded, segmentation shortcuts and delayed mode already
   set, autosave already on. Nothing to configure, no dialog to dismiss.
4. Work the guide. Close the browser tab whenever; the session and the file stay
   put. Come back tomorrow and continue.
5. Click **Submit finished pass**. It checks for empty segments, syncs, and says
   done.
6. Admin ingests, exports gold, deletes the VM.

Steps 3 and 5 are the ones that don't exist today, and they're most of the value.

---

## 7. Deploy and operate

Proposed layout — scripts, not a framework, matching R9:

```
annotation-env/
├── image/
│   ├── build_image.sh        # configure a VM, snapshot it, tag it
│   ├── install_elan.sh       # pinned ELAN 7.x + media deps
│   ├── elan_prefs/           # the golden preferences dir + a note on each setting
│   └── desktop/              # launchers, guide/cheat-sheet HTML, CRD host policy
├── provision/
│   ├── make_kit.py           # manifest + template + media -> kit in GCS
│   ├── new_annotator_vm.sh   # one VM from the image, kit as metadata
│   └── delete_annotator_vm.sh
├── ingest/
│   └── ingest_submissions.py # submission -> data/gold/, then eaf_to_gold.py
└── README.md
```

Operating an assignment is three commands:

```bash
python annotation-env/provision/make_kit.py --case 261456 --annotator jamie
annotation-env/provision/new_annotator_vm.sh --annotator jamie --case 261456
# ... annotator works, submits ...
python annotation-env/ingest/ingest_submissions.py --case 261456 --annotator jamie
```

Terraform is the right call if IT wants the project reproducible from
declaration, and it's the natural companion to access layer (b). For two or three
VMs managed by one person, an instance template plus these scripts is less to
learn, less to break, and reviewable in an afternoon.

**Idle shutdown**, with care: R6 means a session must survive a disconnect for
days, so shut down on *no active session AND no input* for something long (60
minutes), never on disconnect alone. The five-minute rsync means an
over-aggressive shutdown loses minutes at worst — but ELAN state doesn't survive
a stop, and re-finding your place in an encounter is expensive. Err long.

---

## 8. The two things that could sink this — test them before onboarding anyone

Both are cheap to test and expensive to discover after onboarding three people.
So build one image, stand up one VM, and have one annotator actually annotate
one real excerpt on it before anyone else is trained. Budget a day. The point is
to find out whether ELAN-on-a-remote-desktop is genuinely usable for this work
while the only cost of a bad answer is that one day.

**Playback.** ELAN's Linux media stack is historically the least robust of its
three platforms, and this workflow leans on media playback harder than most —
looping short segments, scrubbing, waveform plus video in sync. Test with a real
kit, not a sample file. Mitigations, in order: install VLC and full codecs and
pin ELAN's preferred media framework in the golden prefs; transcode the proxy to
plain H.264 baseline + a separate 16 kHz mono wav (which §4.3 does anyway); and
if a codec still fights, the wav is what the work actually depends on — the video
can degrade further without stopping anyone.

**Latency, and boundary drift.** This one has a data-quality edge, not just a
comfort edge. In segmentation mode the boundary lands when the annotator presses
Enter, so remote-desktop round-trip time shifts **every boundary in the same
direction**. [S§5] allows ~100 ms, so a 20–40 ms RTT is inside tolerance — but
it's systematic, it stacks with human reaction time, and the guide's suggested
200 ms delayed-mode offset was picked on a local machine. So:

- **Deploy in `us-east5` (Columbus).** It's the closest region to Cleveland by a
  wide margin — single-digit-millisecond RTT territory. `us-east4` (Ashburn) is
  the fallback. This is a free decision made once; making it wrong is not free.
- **Measure RTT on the real path, then set delayed mode in the golden prefs** to
  reaction time plus measured latency, and write down what was measured. Then
  the guide can stop asking each annotator to guess.
- **Check it against the audio.** In the trial run, compare a handful of boundaries
  placed on the VM against the same passage placed locally. If drift shows up
  above tolerance, delayed mode absorbs a constant offset — that's exactly what
  it's for.

---

## 9. Cost sketch

Per active annotator, assuming ~8 h/day of real annotation on ~21 days/month.
Order-of-magnitude only — check current rates.

| Item | Monthly |
|---|---|
| `e2-standard-4` (4 vCPU / 16 GB), 8 h/day | ~$23 |
| 100 GB balanced PD (billed while stopped too) | ~$10 |
| Remote-desktop egress (~2 Mbps while connected) | ~$15–20 |
| **Per annotator** | **~$50** |
| GCS, per TB of corpus staged in the bucket | ~$20 |
| HTTPS load balancer + IAP (one, shared across annotators) | ~$18 |

No GPU. Three annotators plus a terabyte lands near $190/month, and the dominant
lever is stopping VMs — an always-on VM triples its own line. Egress is the
line item people don't predict: a remote desktop is a continuous video stream out
of the datacenter, so a 1080p full-video scrub costs real money as well as real
latency. Another reason for the proxy in §4.3.

---

## 10. Deliberately not doing

- **A web annotation tool instead of ELAN** — see §1. It would invalidate the
  standards, the tier scheme, and `eaf_to_gold.py` to save an image build.
- **A shared multi-user annotation host** — §3.
- **Filestore or gcsfuse for media** — §4.1.
- **A real database or a shared manifest** — §4.6. Flat kit/submission JSON with
  a single writer is enough for a handful of assignments, and it keeps the
  manifest's existing single-writer guarantees intact.
- **Serving the whole corpus to every VM.** A VM gets its assigned case and
  nothing else. That's what makes [S§9] and [S§10] structural.
- **Automating assignment.** Who annotates what, and which 20% gets
  double-annotated ([S§10]), is a research judgement made a few times a month.
  A scheduler for that is machinery in search of a problem.

---

## 11. Open questions

1. ~~Data residency~~ — **resolved.** Enterprise Google instance under BAA (§0).
2. **Is Chrome Remote Desktop on the BAA's covered-services list?** (§4.5) The
   only question standing between (a) and (b). Assume no and build (b); if the
   agreement says otherwise, (a) saves a day and $18/month for an identical
   annotator experience. This is a document lookup, not an investigation.
3. **Proxy video resolution** (§4.3) — 480p is a guess. The trial run's real question
   is whether speaker identification survives it; if it doesn't, 720p and a
   larger egress bill.
4. **Does the corpus itself move to GCS**, or does the bucket stay a staging area
   fed from the mini? This design assumes staging, which is the smaller
   commitment and answers the pipeline doc's open storage question (§"Data volume
   & governance") in only one direction. Wholesale migration is a separate
   decision with a much larger IRB surface.
5. **What happens to pass 2.** Adjudication ([S§9]) *does* consult the machine
   draft, so it needs a kit variant that stages the draft `.eaf` alongside the
   frozen pass 1 — the one case where the blind-pass isolation is deliberately
   relaxed. Same environment, different kit; out of scope here, but the kit format
   should not make it awkward.
