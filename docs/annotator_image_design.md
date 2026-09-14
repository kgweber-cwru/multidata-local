# Annotator machine image — design for review

**Status: design only. Nothing here is built.**

The disk image every annotator machine is created from: a Debian desktop with
ELAN 7.1 installed, the project guide on the desktop, and three buttons. An
annotator who logs in should be able to start transcribing immediately.

The environment around it — storage, the network boundary, how annotators
connect, how work gets back — is
[annotation_environment_design.md](annotation_environment_design.md), cited
**[E§n]**. The work it supports is [annotator_guide.md](annotator_guide.md)
(**[G§n]**) and [transcription_standards.md](transcription_standards.md)
(**[S§n]**).

> **This design is deliberately plain.** An earlier draft pre-configured seven
> ELAN preferences, ran five submit-time checks, and locked the desktop down as a
> containment boundary. All three were cut on review (§7). What's left is the part
> that actually earns its keep.

---

## 1. The one idea

**The annotator never creates a file.**

That's the payload. Not the preferences, not the lockdown — the fact that when
they click one button, the right `.eaf` opens with the right template, the right
six tiers, and both media files already attached.

Compare what [G§1] asks for today: File → New, add two media files, select
`elan/template.etf`, then save with the right name in the right folder. That's
four chances to produce a file that looks fine and is quietly wrong — one media
file instead of two, last project's template, a typo'd case number — and every
one of them is invisible until something downstream breaks or, worse, doesn't.

The image removes the whole step. Everything else below is small by comparison,
and should stay small.

---

## 2. What's in the image

### 2.1 Base

| | Choice | Why |
|---|---|---|
| OS | **Debian 12**, 64-bit | Long support window, standard Compute Engine base. |
| Desktop | **Xfce4** | Light and predictable, with no compositing or animation to fight a remote session. |
| Machine size | `e2-standard-4` (4 vCPU, 16 GB) | ELAN is a Java application decoding video and drawing a waveform. 4 vCPU is the cheap safe answer at ~$0.13/hour. No GPU. |
| Disk | 50 GB balanced | OS + ELAN + desktop is ~15 GB; a case kit is ~230 MB ([E§4.1]). Rest is headroom. |
| Region | `us-east5` (Columbus) | Closest region to Cleveland — see §5. |
| Desktop reachability | VNC on **all interfaces**, port 5901 | Not loopback. IAP TCP forwarding does not terminate on the VM — Google's infrastructure connects to the machine's *internal interface*, so a loopback-only listener is unreachable through the tunnel. The boundary is the firewall (IAP's `35.235.240.0/20` only) plus having no external IP, not the bind address. |

Plus media codecs (`vlc`, `libavcodec-extra`) for ELAN's playback, and fonts.

**A browser (`firefox-esr`) is installed, for one reason: to read the guide**
(§2.4) — there has to be something that renders local HTML. Decision 4 (§7) is
what makes that acceptable rather than a hole: annotators are trusted, so the
desktop is kept uncluttered for usability, not as a containment boundary. If
that decision is ever reversed, the browser and the guide viewer change
together. No mail client, for the plain reason that there is nothing for it to
do.

### 2.2 ELAN

**ELAN 7.1, pinned.** Installed from its Linux distribution, which bundles its own
Java runtime, so there's no separate Java to install or patch.

The version goes into the image tag and into each kit's `kit.json`, so a gold
file can always be traced to the ELAN that produced it. [S§12] treats anything
that could change annotation behaviour as a change-control event, and "which ELAN
was this made in" becomes unanswerable the moment it isn't written down.

**Verify 7.1's Linux build plays the proxy video and the wav together** during the
trial run (§5). That's the one thing about the ELAN choice that could force a
change.

### 2.3 ELAN preferences: one setting

**Autosave on, short interval. Everything else stays at ELAN's defaults.**

Autosave earns its place because it deletes a human dependency rather than adding
a preference: [G§1] currently has to nag "save often," and the work spans days
([E§R6]), so occasional loss is otherwise a matter of time.

**Confirmed and shipped as of 2026-09-14.** ELAN keeps its global settings at
`~/.elan_data/elan.pfsx` on Linux, as a flat XML `<preferences>` document of
`<pref key="...">` entries. Autosave is the pair `AutomaticBackupOn` (Boolean)
and `BackUpDelay` (Int, milliseconds, from a fixed set of intervals), shipped as
`true` and `300000` — five minutes, the shortest ELAN's dropdown offers. The
image copies that file into `/etc/skel`, so every new session starts from it.

Two window-geometry settings ship alongside, a small departure from "autosave
only": the capture had ELAN opening at 800x600 on a 1600x1000 desktop, and
leaving it would have every annotator resizing before they could start. That's
function, not taste — and it's coupled to `-geometry` in the VNC unit, noted in
both files. Everything else in the capture was stripped;
`annotation/image/elan_prefs/NOTES.md` records what and why.

**Capture those settings from a clean machine built from this image, never from
a working Mac.** Wrong paths are the obvious reason; the real one is
`FrameManager.RecentFiles`, a list of recently-opened `.eaf` files. Baked into
the image, it would show **every annotator the case IDs of other people's work**
the moment they open ELAN's File menu — which cuts against the blind-pass
isolation the whole environment exists to keep (§5). A clean machine has no such
list. `annotation/image/elan_prefs/NOTES.md` has the capture procedure.

Nothing else does. An earlier draft also set segmentation keystrokes, tier
colours, fonts, waveform zoom, and default directories. Those are either already
the default, or they're taste — and taste imposed on someone who will spend 60+
hours in the tool is a cost, not a service. Ship defaults. Let annotators adjust
whatever they like.

**Delayed mode is a trial-run question, not a build step.** [G§3] says annotators
may set it and suggests experimenting around 200 ms. The concern is real —
boundaries land on the keypress, so a remote session's round-trip time shifts
every boundary in the same direction — but [S§5] allows roughly a tenth of a
second, and `us-east5` should keep the latency well inside that. So: **measure it
once during the trial run** (§5). If it turns out to matter, pick one value, bake
it into the image, and tell everyone that's the value — one number decided once,
rather than each annotator guessing. If it doesn't, leave it default and say so
in the guide.

> Whatever ends up set, write down what and why next to the build script. Not for
> a successor (§7.5) — for you in six months, when "why is autosave at 60
> seconds" is a question with no answer anywhere else.

### 2.4 The guide, on the desktop

[G§"Before you start"] assumes the cheat sheet is printed or on a second screen.
An annotator in one remote desktop session has neither, so the image ships the
guide and the standards as local HTML with a launcher, in a window they can keep
beside ELAN.

Local copies, not links — a guide that needs the network is unavailable exactly
when someone is stuck, and rendering local HTML is the one job the browser in
§2.1 is there to do. Version them with the image: a
guide newer than the machine is a way to follow rules the standards no longer
have.

### 2.5 The three launchers

**① Start annotating.** Opens the staged `.eaf` in ELAN. No file chooser — the
machine knows what's staged. If more than one case is staged (which happens under
one machine per annotator, [E§2]), it asks which, showing case IDs and whether
each is finished.

**② Submit finished pass.** Two checks, then upload:

- **Refuses on any empty segment.** [G§7] already requires this, because empty
  segments become phantom turns in the diarization reference. It's a mechanical
  rule, so it should be mechanical.
- **Warns on annotations outside the excerpt window**, where a case has one
  ([E§4.3]). A warning rather than a refusal, because the window is guidance and
  `.eaf`-level excerpt slicing is deliberately unbuilt — refusing here would
  enforce a boundary the pipeline itself doesn't.

Then it says clearly that **pass 1 is now frozen** ([S§9]), because it is, and
"don't reopen it" is far easier to follow when the machine says so at the moment
it takes effect.

> **If you ever want a third check**, the one I'd add is a warning on
> `[LEARNER_NAME]`-style placeholders. [G§4]/[S§8] say to type real names verbatim
> because redaction happens mechanically later, and a placeholder doesn't fail
> loudly — it quietly scores against a provider that was correct. It's a one-line
> pattern match. Left out for now on the "don't overcomplicate" principle, and
> noted here rather than dropped silently.

**③ Guide & cheat sheet.** Opens §2.4.

### 2.6 Background

- **One cron line** copying the working directory up to that annotator's area in
  the bucket every five minutes ([E§4.6]). Insurance for days of irreplaceable
  human effort sitting on one disk; with object versioning on, a copy that races
  a save is recoverable. One line, not machinery.
- **Nothing else.** No automatic updates, no telemetry, no scheduled reboots. An
  image that changes under a working annotator is a way to get a gold file nobody
  can reproduce.

---

## 3. How the image gets built

One script, run once, producing a tagged image — not a machine configured by
hand.

```
annotation/image/
├── install.sh      # Debian + Xfce + codecs + ELAN 7.1 (checksum verified)
├── elan_prefs/     # the one non-default setting + NOTES.md on what and why
├── desktop/        # the three launchers and the small helpers behind them
└── docs/           # the guide markdown, staged in at build time (gitignored)

annotation/gcp/build_image.sh   # boots a VM, runs install.sh, snapshots, tags
annotation/submit_checks.py     # the checks from §2.5 -- shared with the
                                #   private side, so one copy, not two
```

**Built as of 2026-09-14.** `install.sh` and `build_image.sh` are written and
syntax-checked but have never been run — no project existed to run them against.
[../annotation/README.md](../annotation/README.md) lists what is verified and
what isn't.

The reason to script it isn't automation for its own sake — it's that several
machines built from one image are provably identical. That's the same argument
`elan/README.md` makes for tracking `template.etf` in git: an untracked
configuration gives no guarantee two annotators are working the same way, and the
drift stays invisible until it corrupts an agreement number.

Create a plain Debian VM, run the installer, check it against §6 by hand, then
snapshot to `annotator-v<n>` and record the ELAN version and build date. Packer
would be better if this were rebuilt often; for a handful of rebuilds, a shell
script someone can read start to finish is easier to trust.

**Rebuild rather than patch.** If ELAN needs upgrading or a setting needs
changing, build `annotator-v2` and start new machines from it. Never modify a
running annotator's machine mid-case — that changes the tool underneath work in
progress and breaks the link between a gold file and a recorded image version.

---

## 4. What the image does *not* contain

- **Any case media.** It arrives per assignment, staged from the bucket
  ([E§4.1]). Media baked into an image would be identifiable data sitting in an
  image registry, copied into every machine, impossible to revoke.
- **The manifest, or any credential that could reach it.** Annotator machines
  don't need it and structurally can't have it ([E§2]) — a kit carries the few
  facts they need as flat text.
- **Any machine-generated transcript.** [S§9] is categorical: pass 1 is blind.
  This is the one item on this list that isn't about tidiness — see §5.
- **Access to anything but the bucket.** The machine's identity can reach Cloud
  Storage and nothing else — no project-wide roles, and no route to the open
  internet at all (`setup_project.sh`). Note the honest limit: the machines share
  one service account scoped to the bucket, so what keeps one annotator from
  another's work is **that only their own case is staged on their own machine**,
  not an IAM boundary. That is the part that matters for §5's argument — you
  cannot be unconsciously biased by a file that isn't there — and a per-annotator
  service account with a prefix condition is the upgrade if the posture ever
  needs to resist deliberate access too.

---

## 5. The three things to settle in the trial run

One image, one machine, one annotator, one real excerpt — before anyone else is
onboarded. All three of these are invisible on paper and obvious within an hour
of real use.

**Playback.** ELAN's Linux media support is the least robust of its three
platforms, and this work loops, scrubs, and keeps waveform and video in sync
constantly. The image installs full codecs and the kit ships a modest-resolution
H.264 proxy plus a separate 16 kHz mono wav ([E§4.3]), which avoids most codec
trouble by giving ELAN predictable inputs. **Test with a real kit, not a sample
file.** If a format still fights, remember the wav is what the work depends on —
video only answers "who is talking" and can degrade a long way.

**Latency.** Measure the round-trip time from a real annotator's home connection,
not from your own desk, and settle the delayed-mode question (§2.3) from that
measurement. If you set a value, place a handful of boundaries on the machine and
the same passage locally and compare.

**Blindness is not a trust measure.** Worth being explicit, since annotators are
trusted (§7.4): the per-annotator isolation in [E§5] stays regardless. [S§9]'s
concern is that seeing another transcript biases gold *unconsciously* and "in a
way that can't be detected afterward" — a conscientious annotator who glances at
a colleague's file is just as contaminated as a careless one, and neither would
know. One machine per annotator gives this for free, so it costs nothing to keep.

---

## 6. Acceptance checklist

Done when someone who has never seen the project can, on a fresh machine:

- [ ] Log in and see a desktop with three launchers.
- [ ] Click **Start annotating** and have ELAN open the staged file — **with no
      "locate media" dialog**, both media loaded, waveform drawn.
- [ ] See exactly the six tiers of [S§4], correctly named and empty.
- [ ] Segment and transcribe normally, with playback that holds up under looping.
- [ ] Force-close ELAN after the autosave interval, reopen, find the work there.
- [ ] Disconnect, wait, reconnect, find the session as left.
- [ ] Read the guide in a window beside ELAN.
- [ ] Leave one segment empty, click **Submit**, be refused with the tier and
      time named.
- [ ] Fix it, click **Submit**, be told it worked and pass 1 is frozen.
- [ ] Have that `.eaf` land on the private side and pass `eaf_to_gold.py` with no
      hand-editing.

**Last item first if you want the honest test.** The point of the image is a gold
reference that leaves the pipeline correctly, not a nice desktop.

---

## 7. Decisions, settled 2026-09-14

Recorded because each one cut something, and a future reader should see the choice
rather than wonder what was forgotten.

1. **ELAN 7.1**, pinned (§2.2).
2. **Stay close to defaults** on preferences (§2.3). Four settings ship:
   autosave on, its interval, and a window size that fits the desktop — the
   last because the captured 800x600 would have cost every annotator a resize
   before starting. Cut: tier colours, fonts, waveform zoom, segmentation
   keystrokes, default directories, and everything else that came along in the
   capture. Delayed mode remains a measurement, not a build step.
3. **Warn on excerpt violations, don't refuse** (§2.5). Submit checks cut from
   five to two.
4. **Annotators are trusted.** The desktop is kept uncluttered for usability, not
   as a containment boundary: the earlier draft's clipboard and file-transfer
   lockdown is cut, since the structural property ([E§R1] — data lives on the VM
   and in the bucket, never on a personal machine) does the real work, and a
   clipboard doesn't undermine it. [S§8] tells annotators not to move Layer 1
   files around, and they won't. **Blind-pass isolation is unaffected** — see §5.
   This decision is also what let a browser onto the desktop (§2.1), which the
   guide needs anyway.
5. **If the maintainer is unavailable, work stops.** No bus-factor design. The
   build script and the §2.3 notes exist for the maintainer's own memory, not for
   a handover.

---

## 8. Still open

1. **Does ELAN 7.1's Linux build play the proxy cleanly?** (§2.2, §5) The only
   question that could force a change to the kit format.
2. **Does delayed mode need setting at all?** (§2.3, §5) A measurement, not a
   debate — and there's a reference point now. On a local Mac, with no network
   in the path at all, the settled values were `SegmentationMode.DelayMode=true`
   and `DelayDuration=250` ms — above the ~200 ms [G§3] suggests trying. So 250
   ms is one person's plain reaction time. If the right offset on the VM lands
   near there, latency isn't contributing anything meaningful and delayed mode
   can stay a personal preference rather than an image setting. The measurement
   slot is in `annotation/image/elan_prefs/NOTES.md`.
