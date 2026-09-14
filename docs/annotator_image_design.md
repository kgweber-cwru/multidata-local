# Annotator machine image — design for review

**Status: design only, for review. Nothing here is built.**

This specifies the disk image every annotator machine is created from: a Debian
desktop with ELAN already installed and already configured, the project guide on
the desktop, and three buttons. An annotator who logs in should be able to start
transcribing without opening a settings dialog, locating a file, or being told
which template to use.

The environment this image lives in — storage, the network boundary, how
annotators connect, how work gets back — is
[annotation_environment_design.md](annotation_environment_design.md), cited
**[E§n]**. The work the image has to support is
[annotator_guide.md](annotator_guide.md) (**[G§n]**) and
[transcription_standards.md](transcription_standards.md) (**[S§n]**).

---

## 1. The one idea

**Everything an annotator would otherwise have to configure is decided once, at
image build time, by someone who read the standards.**

That is the whole design. It is worth stating because the alternative — a clean
ELAN install plus good written instructions — looks almost as good and is much
worse in practice. Three reasons, each drawn from the existing guide:

- **A setting the annotator chooses is a setting that varies between
  annotators.** [G§3] currently tells people to experiment with a ~200 ms
  "delayed mode" offset. That offset shifts every segment boundary they place.
  Two annotators picking different values is a systematic difference in the data
  that no later analysis can see or undo. It belongs in the image.
- **A step the annotator performs is a step they can skip.** [G§1] says save
  immediately and save often; [G§7] says check every tier before handing off.
  Autosave and a pre-flight check remove the first entirely and make the second
  mechanical.
- **A file the annotator opens is a file they can open wrong.** [G§1]'s
  File → New flow has them add two media files and pick a template. That's four
  chances to produce a file that looks fine and is subtly wrong — wrong template,
  one media file, wrong case. The image never asks: the file already exists,
  already linked ([E§4.2]).

Everything below follows from that. Where a choice is uncertain, it says so and
says what to check.

---

## 2. What's in the image

### 2.1 Base

| | Choice | Why |
|---|---|---|
| OS | **Debian 12**, 64-bit | Long support window, ELAN ships a Debian-compatible build, and it's a standard Compute Engine base image. |
| Desktop | **Xfce4** | Light, predictable, and no compositing or animation to fight a remote session. GNOME's effects cost bandwidth and buy nothing here. |
| Machine size | `e2-standard-4` (4 vCPU, 16 GB) | ELAN is a Java application decoding video and rendering a waveform. 2 vCPU is probably enough for audio-only work and probably not enough for comfortable video scrubbing; 4 is the cheap safe answer at ~$0.13/hour. No GPU. |
| Disk | 50 GB balanced | OS + ELAN + desktop is ~15 GB; a case kit is ~230 MB ([E§4.1]). The rest is headroom. |
| Region | `us-east5` (Columbus) | Closest region to Cleveland, which matters for keystroke latency — see §5. |

**No browser, no email client, no file manager pointed anywhere interesting, and
no terminal for the annotator account.** Not because annotators can't be trusted,
but because R1 ([E§1]) says media and Layer 1 files don't reach personal
machines, and every one of those is a route off the machine. The desktop should
contain the work and nothing else.

### 2.2 ELAN

**Pinned to one 7.x release**, installed from its Linux distribution, which
bundles its own Java runtime — so there is no separate Java to install, patch, or
have drift out from under the application.

The pinned version goes into the image tag *and* into each kit's `kit.json`, so
every gold file can be traced to the ELAN that produced it. This is not
bureaucracy: [S§12] treats a change that could alter annotation behaviour as a
change-control event, and "which ELAN was this annotated in" is a question that
becomes unanswerable the moment it isn't recorded.

**Media playback is the part to verify, not assume** — see §5.

### 2.3 The pre-configured ELAN preferences

ELAN keeps user settings in a directory in the user's home (`~/.elan_data` on
Linux, as of ELAN 6/7 — **confirm the exact path and file set during the
build**). The image places a prepared copy of that directory in `/etc/skel`, so
every new user session starts from it already configured.

What it should set, and why each one:

| Setting | Value | Why |
|---|---|---|
| Delayed mode | **on**, offset calibrated per §5 | [G§3] makes this the annotator's problem. It shouldn't be: it shifts every boundary they place, so it's a per-deployment constant. |
| Segmentation keystrokes | The two-keystroke scheme [G§3] describes | So the guide's instructions match the machine as shipped, rather than describing a default that might change between ELAN releases. |
| Autosave | **on**, short interval | Deletes [G§1]'s "save often" nag as a human dependency. Work spans days ([E§R6]); relying on a person to remember is a guaranteed occasional loss. |
| Default template | `/srv/multidata/template.etf` | Belt and braces. Kits ship a pre-made file, so nobody should ever need File → New — but if they do, the right template is the default. |
| Default media/working directory | `/srv/multidata/case/` | Any file dialog opens where the work is. |
| Tier colours and fonts | Fixed, legible, consistent | Six tiers ([S§4]) are much easier to scan with stable colours, and consistency between annotators is worth having for free. |
| Waveform display | Sensible default zoom and height | The waveform is the primary working surface in both segmentation and transcription mode. |

> **Write down what each setting is and why, next to the build script.** A
> prepared preferences directory that nobody can read is a liability: the next
> person cannot tell an intentional decision from an accident of whoever's
> machine it was copied from. If a setting has no recorded reason, it should be
> removed rather than inherited.

**Open question for review:** delayed mode plus autosave are clearly right. Tier
colours and waveform zoom are taste, and taste imposed on an annotator who
spends 60+ hours in the tool may be worse than letting them adjust it. Suggestion
— ship them as defaults, and don't try to stop annotators changing anything that
isn't timing-related. Timing settings are data; colours are comfort.

### 2.4 The guide, on the desktop

[G§"Before you start"] assumes the cheat sheet is printed or on a second screen.
An annotator in a single remote desktop session has neither. So the image ships
the guide and the standards as local HTML with a desktop launcher, in a window
they can keep beside ELAN.

Local copies, not links — there's no browser (§2.1), and a guide that needs
network access is a guide that's unavailable exactly when someone is stuck.

**Version them with the image.** A guide that's newer than the machine an
annotator is working on is a way to produce a file that follows rules the
standards no longer have.

### 2.5 The three launchers

The whole interface. Nothing else on the desktop.

**① Start annotating.** Opens the staged `.eaf` in ELAN. No file chooser: the
machine knows which case is staged. If more than one case is staged — which
happens under one-machine-per-annotator ([E§2]) — it asks which, showing case
IDs and whether each is finished.

**② Submit finished pass.** Runs the checks below, uploads, and reports. This is
also where [G§7]'s hand-off checklist stops being a human ritual:

- **Refuse on any empty segment.** [G§7] says an empty segment must be
  transcribed or deleted, because empty segments become phantom turns in the
  diarization reference. That's a mechanical check, so it should be mechanical.
- **Refuse if no tier has any content** — almost certainly a mis-click rather
  than a finished pass.
- **Warn, don't refuse, on a bare `[unintelligible]` count above some
  threshold.** High uncertainty is legitimate ([G§5] — "never guess"), so this
  must never block. But an unusual count is worth a second look, most often at
  the audio rather than the annotator.
- **Warn on `[LEARNER_NAME]`-style placeholders.** [G§4]/[S§8] say type real
  names verbatim; redaction happens mechanically later. A placeholder means a
  well-intentioned annotator has broken scoring, and it's cheap to catch here.
- **Then say clearly that pass 1 is now frozen** ([S§9]), because it is, and the
  guide's "don't reopen it" rule is much easier to follow when the machine says
  so at the moment it takes effect.

**③ Guide & cheat sheet.** Opens §2.4.

> **Open question for review:** should ② also refuse to submit when a case has an
> excerpt window ([E§4.3]) and there are annotations outside it? It's a real
> error and mechanically detectable. I'd make it a *warning* rather than a
> refusal — the window is advisory guidance to the annotator, `.eaf`-level
> excerpt slicing is deliberately unbuilt (see `current_status.md`), and a
> refusal here would be enforcing a boundary the pipeline doesn't itself enforce.

### 2.6 What runs in the background

- **A periodic upload of the working directory**, every five minutes, to that
  annotator's own area in the bucket ([E§4.6]). Insurance, not the hand-off —
  with object versioning on, a save that races an upload is recoverable.
- **Nothing else.** No updates that could change ELAN mid-campaign, no telemetry,
  no scheduled reboots. An image that changes under a working annotator is a way
  to get an unreproducible gold file.

---

## 3. How the image gets built

**One script, run by one person, producing a tagged image. Never configured by
hand in place.** The point isn't automation for its own sake — it's that several
machines built from one image are provably identical, which is the same argument
`elan/README.md` makes for tracking `template.etf` in git: an untracked
configuration offers no guarantee that two annotators are working the same way,
and the drift is invisible until it corrupts an agreement number.

```
annotation-env/image/
├── build_image.sh       # boot a VM, run the installers, snapshot, tag
├── install_base.sh      # Debian + Xfce + media codecs, strip what's unneeded
├── install_elan.sh      # pinned ELAN release, verified checksum
├── elan_prefs/          # the prepared preferences dir + NOTES.md on each setting
├── desktop/             # the three launchers, guide/standards HTML
└── checks/              # the submit-time checks from §2.5
```

Suggested build flow: create a plain Debian VM, run the install scripts, verify
by hand against the §6 checklist, then snapshot it to an image named
`annotator-v<n>` and record the ELAN version and build date alongside. Packer
would also work and is the better answer if this is ever rebuilt often; for an
image rebuilt a handful of times, a shell script someone can read start to finish
is easier to trust.

**Rebuild rather than patch.** If ELAN needs upgrading or a preference needs
changing, build `annotator-v2` and start new machines from it. Never modify a
running annotator's machine mid-case: that changes the tool underneath work in
progress, and it breaks the one-to-one link between a gold file and a recorded
image version.

---

## 4. What the image does *not* contain

Worth stating, because each is a plausible-looking addition that would hurt.

- **Any case media.** Media arrives per assignment, staged from the bucket
  ([E§4.1]). An image with media in it would be a copy of identifiable data
  sitting in an image registry, duplicated into every machine, and impossible to
  revoke.
- **The manifest, or any credential that could reach it.** Annotator machines
  don't need it and structurally can't have it ([E§2]). A kit carries the five
  facts they need as flat text.
- **Any machine-generated transcript.** [S§9] is categorical: pass 1 is blind.
  A draft on the machine is the one thing that could quietly destroy the
  benchmark's validity, so the image should not even contain the tooling to
  produce one.
- **Broad read access to the bucket.** The machine's identity can read its own
  annotator's kits and write its own work area. Nothing else. That's what makes
  blindness between annotators ([S§10]) structural rather than a promise.
- **A browser or general-purpose tools** — §2.1.

---

## 5. The two risks, and how the image addresses them

Both are the ones flagged in [E§8], restated here because they're properties of
the image rather than the environment. **Both need one real annotator on one real
case before anyone else is onboarded.**

### Media playback

ELAN's Linux media support is historically the least robust of its three
platforms, and this work leans on playback harder than most: looping a short
segment repeatedly, scrubbing, keeping waveform and video in sync.

What the image does about it, in order:

1. Install a full set of media codecs and libraries, and pin ELAN's preferred
   media framework in the prepared preferences rather than letting it pick.
2. Rely on the kit's **proxy video** ([E§4.3]) — a modest-resolution H.264 file
   plus the separate 16 kHz mono wav. Predictable inputs avoid most codec
   trouble, and the transcode is happening anyway for bandwidth reasons.
3. If a format still fights, remember the wav is what the work actually depends
   on. Video's job is answering "who is talking" ([E§4.3]); it can degrade a long
   way without stopping anyone.

**Verify with a real kit, not a sample file.** This is the single most likely
thing to go wrong, and it is invisible until someone tries to work.

### Keystroke latency and boundary drift

This one has a data-quality edge, not just a comfort edge. In segmentation mode
the boundary lands when the annotator presses a key, so a remote session's
round-trip time shifts **every boundary in the same direction**.

[S§5] allows roughly a tenth of a second, so the region choice (§2.1) should keep
this inside tolerance — but it is systematic, and it stacks with human reaction
time. So:

1. **Measure the actual round-trip time** on the real path, from a real
   annotator's home connection, not from your own desk.
2. **Set delayed mode in the prepared preferences** to reaction time plus that
   measurement, and record what was measured and when.
3. **Check it against the audio**: place a handful of boundaries on the machine
   and the same passage locally, and compare. Delayed mode absorbs a constant
   offset, which is exactly what this is.

Then [G§3] can stop asking each annotator to guess, and the guide should be
updated to say the setting is already correct.

---

## 6. Acceptance checklist

The image is done when a person who has never seen the project can, on a fresh
machine from it:

- [ ] Log in and see a desktop with exactly three launchers.
- [ ] Click **Start annotating** and have ELAN open the staged file — **with no
      "locate media" dialog**, both media files loaded, waveform drawn.
- [ ] See exactly the six tiers of [S§4], correctly named, empty.
- [ ] Enter segmentation mode and place boundaries with the [G§3] keystrokes,
      with delayed mode already on.
- [ ] Stop typing for the autosave interval, force-close ELAN, reopen, and find
      the work there.
- [ ] Disconnect, wait, reconnect, and find the session exactly as left.
- [ ] Open the guide and cheat sheet in a window beside ELAN.
- [ ] Find no browser, no terminal, and no route to copy a file off the machine.
- [ ] Leave one segment empty, click **Submit**, and be refused with a message
      that names the tier and time.
- [ ] Fix it, click **Submit**, and be told it succeeded and pass 1 is frozen.
- [ ] Have the submitted `.eaf` land on the private side and pass
      `eaf_to_gold.py` without hand-editing.

Last item first if you want the honest test: **the point of the image is a gold
reference that exits the pipeline correctly**, not a nice desktop.

---

## 7. Open questions for review

1. **Which ELAN 7.x release**, and does its Linux build play the proxy format
   cleanly? (§2.2, §5) Determines whether the proxy needs re-specifying.
2. **How much preference-setting is too much?** (§2.3) Timing settings are data
   and should be fixed. Colours and zoom are comfort. My suggestion is to ship
   defaults and not police the comfort ones.
3. **Excerpt-window enforcement at submit** — warn or refuse? (§2.5) I'd warn.
4. **Does the annotator account need any terminal access at all?** I've said no.
   If a real annotator hits something a launcher can't fix, the answer should be
   an admin connecting, not the annotator debugging — but that's a support
   commitment worth agreeing to before promising it.
5. **Who rebuilds the image if the maintainer is unavailable?** The build script
   plus the notes in §2.3 are the answer, which is why both need to be readable
   by someone who didn't write them.
