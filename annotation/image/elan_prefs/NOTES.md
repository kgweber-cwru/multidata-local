# ELAN preferences shipped in the image

**Put the *contents* of a clean machine's `~/.elan_data` in this directory** —
not a directory or file called `.elan_data`. `install.sh` copies what's here
into `~/.elan_data/` in `/etc/skel`, so every new session starts from it.

Right now this directory holds nothing but these notes, so **autosave is not
set** and `install.sh` says so loudly during the build. The image works without
it; the annotator guide's "save often" just stays the annotator's problem.

---

## Capture from a clean Linux machine, not from a working Mac

The tempting shortcut is to copy your own preferences across. Don't — and not
only for path hygiene:

- **Paths.** `MediaDir`, `TemplateDir`, and `LastUsedEAFDir` would all point at
  `/Users/<you>/projects/multidata-local/...`, which doesn't exist on the VM.
- **`FrameManager.RecentFiles` is the real reason.** It's a list of every `.eaf`
  recently opened, and baked into the image it would show **every annotator the
  case IDs of other people's work** the moment they open ELAN's File menu. That
  brushes against the blind-pass isolation the whole environment is built to
  keep ([transcription_standards.md](../../../docs/transcription_standards.md)
  §9/§10). A clean machine has no such list.
- **Window geometry and page formats** are captured from whatever screen you
  were using and mean nothing on a 1600x1000 VNC desktop.

### The procedure

1. Build the image once and make a machine from it
   (`annotation/gcp/build_image.sh`, then `new_vm.sh`).
2. Connect, open ELAN, and set **only** the things in the table below. Touch
   nothing else — a setting you didn't mean to ship is worse than a default.
3. Close ELAN so it writes its preferences out.
4. Copy the contents of `~/.elan_data` off the machine and commit them here.
5. Build the next image version. That one has the settings baked in.

Step 4 off a machine with no public IP:

```bash
gcloud compute scp --recurse --tunnel-through-iap --zone us-east5-a \
  annotate-<you>:~/.elan_data/. annotation/image/elan_prefs/
```

Then **read what you copied** and delete anything that isn't in the table. If a
`FrameManager.RecentFiles` list appears, remove it.

---

## What to set

Key names confirmed from a real ELAN preferences file (ELAN's format is a flat
XML `<preferences>` document of `<pref key="...">` entries).

| Setting in ELAN's UI | Key | Value | Why |
|---|---|---|---|
| Automatic backup, on | `AutomaticBackupOn` | `true` | The only setting this image really needs. Removes a human dependency instead of adding a preference: the guide has to nag "save often", and the work spans days. |
| Automatic backup, interval | `BackUpDelay` | the shortest the dropdown offers | ELAN offers a fixed set of intervals rather than free text. A working Mac had `600000` (10 minutes); shorter is better here, since a lost session is a lost afternoon. |

**Nothing else.** Tier colours, fonts, waveform zoom, segmentation keystrokes,
and default directories were all cut on review — see
[../../../docs/annotator_image_design.md](../../../docs/annotator_image_design.md)
§2.3 and §7.2. Either they're already the default, or they're taste, and taste
imposed on someone spending 60+ hours in the tool is a cost rather than a
service.

---

## Delayed mode: still a measurement, with a reference point now

Not set here yet, on purpose — image design §5 makes it a measurement rather
than a guess.

**Useful data point:** on a local Mac, working directly on the audio, the
settled values were `SegmentationMode.DelayMode=true` and
`SegmentationMode.DelayDuration=250` (ms) — a fair bit above the ~200 ms the
guide suggests experimenting with. So 250 ms is one person's reaction time with
**no** network in the path.

That makes the VM comparison concrete: measure the round-trip time from a real
annotator's connection, and if the right offset lands near 250 ms, latency is
not doing anything meaningful and delayed mode can stay a personal preference.
If it needs to be materially higher, set one value here for everyone and record
the measurement below.

**Measured round-trip time:** _not yet measured_

Related keys, for when you get there: `SegmentationMode.DelayMode` (Boolean),
`SegmentationMode.DelayDuration` (Long, milliseconds), `SegmentationMode.Mode`
(String — a Mac had `non-adjacent`).

---

## Record what you shipped

Whenever this directory changes, note here what was set, on which ELAN version,
and why. A preferences blob nobody can read is a liability — this file is how
the next person, or you in six months, tells a deliberate setting from an
accident of whoever's machine it came off.

| Date | ELAN | What changed |
|---|---|---|
| — | — | nothing shipped yet |
