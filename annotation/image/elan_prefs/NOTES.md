# ELAN preferences shipped in the image

**One setting is deliberately changed from ELAN's defaults: autosave.**
Everything else is left alone. See `docs/annotator_image_design.md` §2.3 for why
the earlier, longer list was cut.

| Setting | Value | Why |
|---|---|---|
| Autosave | **on**, 60 seconds | Removes a human dependency rather than adding a preference. The annotator guide currently has to nag "save often", and the work spans days, so occasional loss is otherwise a matter of time. |

**Not set, on purpose:** tier colours, fonts, waveform zoom, segmentation
keystrokes, default directories. Either already the default, or taste — and
taste imposed on someone spending 60+ hours in the tool is a cost, not a
service. Annotators can change whatever they like.

**Delayed mode is not set here yet.** It is a measurement, not a guess: see
`docs/annotator_image_design.md` §5. Measure the round-trip time from a real
annotator's connection, then either set one value for everyone here, or leave it
default and say so in the guide. Whichever you do, record the measurement in
this file.

---

## This directory is empty until someone fills it in

**Unverified, and deliberately not faked.** ELAN keeps its preferences in
`~/.elan_data` on Linux, but the exact filename and format for the autosave
setting were not confirmed when this was written, and inventing an XML file that
ELAN silently ignores would be worse than an empty directory — it would look
done.

So: while building the image, set autosave by hand in ELAN once, then copy the
resulting file(s) out of `~/.elan_data` into this directory and commit them.
`install.sh` will warn loudly if this directory has nothing in it, which is the
intended behaviour until the step above is done.

Record here what you copied and what version of ELAN produced it — a
preferences blob nobody can read is a liability, and this file is the reason the
next person (or you in six months) can tell an intentional setting from an
accident.
