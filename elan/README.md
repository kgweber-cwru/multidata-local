# elan/

`template.etf` — the ELAN template every gold annotation starts from.

**This is a standard.** It lives here, tracked in git, rather than
under `data/` (which is gitignored) precisely so that two annotators on two
machines are provably using the same tier set. An untracked template offers no
such guarantee, and a tier-naming drift between annotators is invisible until it
corrupts an inter-annotator agreement number.

Six tiers, matching
[../docs/transcription_standards.md](../docs/transcription_standards.md) §4:

| Tier | Holds |
|---|---|
| `LEARNER` | the student |
| `PATIENT` | the standardized patient |
| `PRECEPTOR` | the supervising clinician |
| `ANNOUNCEMENT` | PA / station timing calls |
| `OUTSIDE_ROOM` | intelligible hallway bleed |
| `NOTES` | sparse annotator observations |

Annotators don't use it directly: `annotation/make_kit.py` builds each
pass-1 file from it (empty for a blind pass, pre-filled with a machine draft
for a seeded one; [transcription_standards.md](../docs/transcription_standards.md) §9),
with the media already linked. A seeded file may carry a seventh, temporary
`UNASSIGNED` tier, which must be empty before hand-in and never reaches gold.
See [../docs/annotator_guide.md](../docs/annotator_guide.md), Step 1.

> **Changing the tier set is a standards change**. Bump
> `transcription_standards.md` §12 and say what it invalidates.
