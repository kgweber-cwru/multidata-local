# Annotation Environment — design

A self-contained, GCP-hosted virtual desktop that hands an annotator a ready-to-
work ELAN session: correct template, correct tiers, media already linked, nothing
to install, nothing to configure, and no encounter media on a personal machine.

**Status: design only.** Nothing here is built. Read
[current_status.md](current_status.md) for what *is* built. The companion
[annotator_image_design.md](annotator_image_design.md) specifies the machine
image this design assumes.

**Scope this is sized for (2026-09-14).** The first gold campaign is **5–10
hours of video** — roughly 10–20 cases, a small number of annotators, a few
weeks of work. That is ~4 GB of media in total and about 60–100 hours of human
annotation effort. Every choice below is made for *that*, not for the full
1,307-case corpus: the scarce resources are annotator time and setup time, not
bytes or dollars. Where a decision would change at larger scale, it says so.

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

## 2. The network boundary, and the shape it forces

The two machines that run the pipeline sit on a **private network that can reach
out but cannot be reached in.** Annotators are at home or on campus, and
**neither position lets them see that private network.** Those two facts together
decide the shape of everything else, so state them plainly:

- The pipeline cannot serve annotators directly. Not "inconveniently" — at all.
- Annotators must therefore work somewhere reachable from anywhere, which means
  the cloud environment this document describes.
- Anything the annotators need must be **pushed out** from the private side.
  Nothing can be pulled in from outside.

The relief is how little has to cross. **Annotators do not need the manifest.**
Everything an annotator needs to know is about five facts — which case, which two
media files, which template, which time window — and everything they produce is
one ELAN file plus who did it and when. The `gold` table is written by
`eaf_to_gold.py` on the private side, *after* the file comes back.

So the boundary carries, per case, **~230 MB out and a few hundred KB back**:
about 4 GB outbound for the whole first campaign, a dozen-odd times, a few
hundred KB returning. That is small enough and rare enough to be **two commands
a human runs**, in the one direction that works. It needs no tunnel into the
private network, no VPN, no sync daemon, and no always-on connection.

> **The design deliberately keeps it that way.** Automating this boundary would
> mean either opening an inbound path to the private network or granting a cloud
> service credentials to reach in. Both are real risk for a transfer that takes
> ten seconds by hand a dozen times. If the campaign ever grows to hundreds of
> cases, revisit — but revisit it as "should the private side push on a
> schedule," never as "should the cloud side be able to pull."

### Architecture at a glance

```
  PRIVATE NETWORK (outbound only — GCP cannot reach in)
  ┌───────────────────────────────────────────────────┐
  │ Mac mini — single writer, holds all the state     │
  │   manifest.sqlite   data/raw/   data/audio/       │
  │   elan/template.etf                               │
  │                                                   │
  │  make_kit.py  ─────────────┐   ingest_submissions.py
  └────────────────────────────┼──────────────▲───────┘
                               │              │
        ···························│··············│························
          the only boundary:       │  ~230 MB out │  a few hundred KB back,
          pushed by hand, outbound  │  per case    │  pulled by hand
        ···························│··············│························
                               │              │
                        ┌──────▼──────────────┴──────┐
                        │  GCS bucket (us-east5)     │
                        │  kits/<case>/<annotator>/  │  ← read by one VM only
                        │  work/<case>/<annotator>/  │  ← written by one VM only
                        └──────▲──────────────┬──────┘
                               │ stage on     │ sync every 5 min
                               │ assignment   │
                        ┌──────┴──────────────▼──────┐
   annotator, anywhere  │  Annotator VM (one each)   │
   ─── private tunnel ──▶  Debian + Xfce + ELAN 7.x  │
   (no data leaves it)  │  /srv/multidata/…          │  ← canonical mount path
                        └────────────────────────────┘
```

**One VM per annotator**, kept for the length of the campaign, with cases staged
onto it one at a time — not one VM per case. At 10–20 cases that's two or three
machines instead of twenty, and the property that matters is unchanged: each
annotator has their own machine, so nobody can see anyone else's work (§5). Delete
the machines when the campaign ends.

At full-corpus scale, go back to one VM per assignment — it bounds how much
material sits on any one machine. At 10–20 cases that bound buys nothing, because
an annotator is allowed to see their own completed work anyway.

---

## 3. Why a machine per annotator

A single shared annotation host that everyone logs into is the obvious-looking
alternative, and it is worse on every axis that matters here:

- **R5 falls out for free.** A VM only ever has one annotator's assigned case
  staged on it, and its service account can only read that case's kit prefix. On
  a shared host, "don't open the other annotator's folder" is a request.
- **Cost control is per-annotator.** Annotators work in bursts of a few hours.
  A stopped VM costs disk only. A shared host is always on.
- **Teardown is a real erasure.** Delete the VM and its disk and the Layer 1
  copy is gone. On a shared host, cleanup is a script you have to trust.
- **Blast radius.** A misconfigured shared host exposes the whole corpus.

The cost is several machines built from the same image, which is why
[annotator_image_design.md](annotator_image_design.md) makes the image a build
artifact with a version tag rather than something configured by hand per machine.

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
| Local disk, staged from GCS | **Chosen.** Native seek performance, least privilege, costs pennies. A kit is small: measured on the real corpus, a 32-minute encounter is ~143 MB of H.264 plus an ~88 MB wav, so ~230 MB staged, or well under 100 MB with the §4.3 proxy. Seconds, not minutes. |

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
script and never hand-edited in place. It carries Debian, a light desktop, a
pinned ELAN, a pre-configured set of ELAN preferences, the annotator guide, and
three launchers — and nothing else.

**Specified in full in
[annotator_image_design.md](annotator_image_design.md).** Two points belong here
because the rest of this document leans on them:

- **The pre-configured ELAN preferences are the payload of R2.** "No configuring
  ELAN" is not achieved by better instructions; it is achieved by shipping a
  preferences directory that already has segmentation shortcuts, delayed mode,
  and autosave set, so the annotator never opens a settings dialog.
- **The ELAN version is pinned and recorded.** It goes in the image tag and into
  `kit.json`. An ELAN upgrade is a change-control event under [S§12] the same way
  a model version is — an unpinned upgrade partway through a corpus is exactly the
  drift the pipeline doc warns about.

### 4.5 How annotators reach their machine

**Recommendation: a private tunnel, not a web address.** This reverses an earlier
draft of this document, and the reason is scope — see the note at the end.

The annotator installs two things once: Google's command-line tool, and a desktop
viewer. They then run a short script you hand them, which opens a private tunnel
from their own machine to their VM and launches the viewer. Google's
Identity-Aware Proxy carries that tunnel (`gcloud compute start-iap-tunnel`), so:

- **The VM has no public address and no open ports.** Nothing about it is
  reachable from the open internet. The tunnel is authorized by the annotator's
  own institutional Google account, and IAM decides whether they get in.
- **Every component is a Google Cloud service** the BAA covers. Nothing about
  the session leaves that perimeter.
- **Every connection is an identity event against a named person** — who
  connected to which machine, when (§5).
- **It costs nothing to stand up.** No load balancer, no certificate, no DNS, no
  Terraform. A handful of `gcloud` commands and a firewall rule that permits
  only IAP's own address range.
- **Clipboard and file copying are yours to switch off** in the desktop server's
  own configuration, rather than through a policy applied on the annotator's
  computer. Switch both off: "off personal machines" (R1) has to include "can't
  be dragged onto one".

The cost is a **one-time 15-minute screen share per annotator** to get the two
installs and the script working on their machine. With two or three annotators
that is plainly cheaper than a day of your own setup time, and it is the whole
of the friction — after that, they run one script and get a desktop.

> **What changed.** An earlier draft recommended putting an HTTPS load balancer
> with Identity-Aware Proxy in front, so annotators would only need to open a URL
> — no installs at all. That is genuinely nicer for the annotator and it is the
> right answer at scale: it costs about a day of setup and ~$18/month standing,
> which amortizes to nothing across dozens of annotators and hundreds of cases.
> Across **two or three annotators and 10–20 cases it does not amortize at all.**
> So: tunnel now, load balancer if the operation grows. The swap touches only the
> launcher script and one firewall rule — storage, kits, the image, and the submit
> path are identical either way.

> **Chrome Remote Desktop is the option to avoid**, despite being the easiest to
> set up (no networking whatsoever). A remote-desktop session carries the pixels
> of identifiable video and the keystrokes of a transcript full of real names, and
> CRD routes them through a Chrome relay — **a Chrome service, not a Google Cloud
> one, and so almost certainly not on the Cloud BAA's covered-services list.**
> Access control isn't the issue; annotators are on institutional accounts either
> way. Whether that traffic is inside the agreement is. Verify against the actual
> agreement if you want to reconsider, but treat "not covered" as the working
> assumption — that is the direction where guessing wrong is expensive.

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
| [S§8] Layer 1 stays on managed storage | No public IP; egress via Cloud NAT only (or none at all, if the image needs no runtime internet). No file transfer, no clipboard (§4.5). No browser on the desktop.
Nothing inbound to the private network at all (§2). Data path is bucket ↔ VM, both inside the project. |
| [S§8] Layer 2 is derived, never hand-edited | Unchanged: `eaf_to_gold.py` runs admin-side, off the submitted `.eaf`. |
| [S§4] identical tier set | Kits are generated from the tracked `elan/template.etf` by one script. An annotator cannot start from anything else, because they never create a file. |
| BAA — demonstrable access records | **Enable Cloud Audit Logs Data Access logging on the bucket.** It is off by default for GCS, which means the record of who read which case's media does not exist unless someone turns it on. Combined with the tunnel's per-connection identity events (§4.5), "who accessed case 261456, when" becomes answerable rather than inferred. Turn it on when the bucket is created, not after the first question about it. |

VPC Service Controls would draw a perimeter around the bucket so a stolen
credential couldn't pull data out of the org at all. It's org-level configuration
with org-level blast radius, so it's IT's to apply rather than yours to
self-serve — but in an enterprise org already operating under BAA, a perimeter
around the project is a normal request rather than an exotic one, and it's worth
asking for while the project is being created instead of retrofitting.

---

## 6. Annotator experience, end to end

**Once, at onboarding** (15 minutes, with you on a screen share): install
Google's command-line tool and a desktop viewer, and save the connect script you
hand them. Done forever.

**Then, per case:**

1. Email: "case 261456 is on your machine — go ahead."
2. Run the connect script. A desktop appears with three icons.
3. Click **Start annotating**. ELAN opens on `261456.pass1.eaf`: six empty tiers,
   waveform loaded, video loaded, segmentation shortcuts and delayed mode already
   set, autosave already on. Nothing to configure, no dialog to dismiss.
4. Work the guide. Disconnect whenever; the session and the file stay put. Come
   back tomorrow and continue where you were.
5. Click **Submit finished pass**. It checks for empty segments, uploads, and
   says done.
6. You pull the submission down, export gold, and stage their next case onto the
   same machine.

Steps 3 and 5 are the ones that don't exist today, and they're most of the value.

---

## 7. Deploy and operate

Proposed layout — scripts, not a framework, matching R9. The `image/` contents
are specified in [annotator_image_design.md](annotator_image_design.md).

```
annotation-env/
├── image/                    # see annotator_image_design.md
├── provision/
│   ├── make_kit.py           # manifest + template + media -> a kit, locally
│   ├── push_kit.sh           # kit -> GCS, and stage it onto an annotator's VM
│   ├── new_annotator_vm.sh   # one VM from the image, for one annotator
│   ├── connect.sh            # handed to the annotator: tunnel + viewer
│   └── delete_annotator_vm.sh
├── ingest/
│   └── pull_submissions.py   # submission -> data/gold/, then eaf_to_gold.py
└── README.md
```

**Once per annotator:**

```bash
annotation-env/provision/new_annotator_vm.sh --annotator jamie
# hand them connect.sh + the two installs, once
```

**Per case, three commands:**

```bash
python annotation-env/provision/make_kit.py --case 261456 --annotator jamie
annotation-env/provision/push_kit.sh --case 261456 --annotator jamie
# ... annotator works, submits ...
python annotation-env/ingest/pull_submissions.py --case 261456 --annotator jamie
```

All three run on the private side and only ever reach *outward*, which is the
only direction that works (§2).

Terraform is the right call if IT wants the project reproducible from a
declaration, and it becomes the natural companion to the load-balancer variant of
§4.5. For two or three machines managed by one person, an instance template plus
these scripts is less to learn, less to break, and reviewable in an afternoon.

**Stopping machines to save money.** At this scale, don't bother automating it.
The whole campaign costs about $40 (§9), so an idle-shutdown rule that
accidentally interrupts someone mid-encounter costs more in annoyance than it
saves in dollars. Stop the machines by hand when a campaign pauses. If this ever
runs continuously with many annotators, add idle shutdown then — and make it
shut down on *no connection AND no input for 60 minutes*, never on disconnect
alone, because R6 means a session has to survive days of elapsed time.

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

Sized for the actual first campaign: **5–10 hours of video, ~10–20 cases, two or
three annotators, a few weeks.** 10 hours of material at 6–10x realtime is
60–100 hours of annotation work. Order-of-magnitude only — check current rates.

| Item | Whole campaign |
|---|---|
| `e2-standard-4` (4 vCPU / 16 GB) × ~80 annotator-hours | ~$11 |
| 50 GB disk × 2–3 machines × ~6 weeks (billed while stopped too) | ~$15 |
| Egress from desktop streaming (~2 Mbps while connected) | ~$9 |
| GCS for ~4 GB of kits and submissions | pennies |
| **Total, for the entire first gold campaign** | **~$35–40** |

Not per month — **total.** No GPU, no load balancer, no Cloud SQL.

**So stop optimizing for money and optimize for your setup time.** That single
fact drives the recommendations above: a tunnel instead of a load balancer (§4.5),
one machine per annotator instead of per case (§2), no idle-shutdown automation
(§7), and no pipeline across the network boundary (§2). Every one of those trades
infrastructure you'd have to build for a small amount of money or a one-time
15-minute call.

The numbers only change shape at full-corpus scale, where the two lines that grow
are **egress** (a remote desktop is a continuous video stream out of the
datacenter — another reason for the proxy video in §4.3) and **machine-hours**
(where stopping idle VMs starts to matter, and the load-balancer variant of §4.5
starts to amortize). Neither is worth engineering for now.

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
- **Automating the network boundary** — §2. The transfer is ~230 MB out and a few
  hundred KB back, a dozen times. Automating it would mean opening an inbound
  path to the private network or handing a cloud service credentials to reach in.
  Not worth it for a ten-second manual push.
- **Giving annotator machines database access.** They don't need it: a kit carries
  the five facts an annotator needs as flat text, and the `gold` table is written
  on the private side after the file comes back (§4.6). This was already the right
  call for isolation reasons; the network boundary makes it structural.
- **Moving the manifest to Postgres for this.** It's the right destination for the
  two-machine pipeline, and it contributes nothing to annotation. See
  [current_status.md](current_status.md) for the sequencing.
- **An idle-shutdown rule** — §7. At ~$40 a campaign it would cost more in
  interrupted sessions than it saves.

---

## 11. Open questions

1. ~~Data residency~~ — **resolved.** Enterprise Google instance under BAA (§0).
2. ~~Can the private network serve annotators?~~ — **resolved, no.** It can
   reach out but not be reached in, and annotators at home or on campus cannot
   see it either way (§2). That's what makes the cloud environment necessary
   rather than optional, and what keeps the boundary one-directional.
3. **Is Chrome Remote Desktop on the BAA's covered-services list?** (§4.5) Only
   matters if you want to skip the tunnel's two client installs. Assume no. This
   is a document lookup, not an investigation.
4. **Proxy video resolution** (§4.3) — 480p is a guess. The trial run's real
   question is whether speaker identification survives it; if it doesn't, 720p
   and a slightly larger egress bill.
5. **What happens to pass 2.** Adjudication ([S§9]) *does* consult the machine
   draft, so it needs a kit variant that stages the draft `.eaf` alongside the
   frozen pass 1 — the one case where the blind-pass isolation is deliberately
   relaxed. Same environment, different kit; out of scope here, but the kit
   format should not make it awkward.
