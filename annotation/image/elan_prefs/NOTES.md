# ELAN preferences shipped in the image

**Put the *contents* of a clean machine's `~/.elan_data` in this directory** —
not a directory or file called `.elan_data`. `install.sh` copies what's here
into `~/.elan_data/` in `/etc/skel`, so every new session starts from it.

`install.sh` warns loudly if this directory is empty — which, as of
2026-09-14, it no longer is. See "What is shipped" below.

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

## What is shipped, and what was stripped

`elan.pfsx` is here. **Confirmed**: ELAN keeps its global preferences at
`~/.elan_data/elan.pfsx` on Linux, as a flat XML `<preferences>` document of
`<pref key="...">` entries. The exact path and filename were an open question
when this file was first written; they aren't now.

Captured from a machine built from `annotator-v2`, then trimmed from nine
settings to four.

**Kept:**

| Key | Value | Why |
|---|---|---|
| `AutomaticBackupOn` | `true` | The reason this file exists. Removes a human dependency instead of adding a preference: the guide has to nag "save often", and the work spans days. |
| `BackUpDelay` | `300000` | Five minutes — the shortest interval ELAN's dropdown offers. A working Mac had `600000`; shorter is better here, since a lost session is a lost afternoon. |

**Changed, not kept:**

| Key | Captured | Shipped | Why |
|---|---|---|---|
| `FrameSize` | `800,600` | `1560,940` | 800x600 is cramped for ELAN's multi-pane layout and would have every annotator resizing the window before they could start. **Coupled to `-geometry` in `desktop/vncserver@.service`** — change one, check the other. |
| `FrameLocation` | `400,186` | `20,20` | Same reason; opens near the top-left of the 1600x1000 desktop. |

**Removed:**

| Key | Why |
|---|---|
| `FrameManager.RecentFiles` | Held `/srv/multidata/case/255050/255050.pass1.eaf`. This is the one that matters: shipped in the image it would show **every annotator a case id that wasn't theirs** the moment they opened ELAN's File menu. |
| `EditPreferencesDialog.Bounds` | Where a dialog happened to sit on the capture machine. Noise. |
| `Recognizer.ReduceFilePrompts` | Nothing here uses ELAN's recognizers. |
| `Locale` | Captured as `en,` with an empty country — ELAN's default, said oddly. |

The rule for anything added later: if you can't say what it does and why this
project wants it, take it out. Four settings someone can read beats nine that
came along for the ride.

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
| 2026-09-14 | 7.1 | First prefs shipped. Autosave on at 5 minutes; window sized for the 1600x1000 desktop. Captured from `annotator-v2`, trimmed nine settings to four. |
