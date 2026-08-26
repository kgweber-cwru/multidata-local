# elan/

`template.etf` — the ELAN template every gold annotation starts from.

**This is a standard, not data.** It lives here, tracked in git, rather than
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

Usage is **File → New**, adding both media files and selecting this template —
see [../docs/annotator_guide.md](../docs/annotator_guide.md) §1.

> **Changing the tier set is a standards change**, not a file edit. Bump
> `transcription_standards.md` §12 and say what it invalidates.
