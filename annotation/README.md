# annotation/

Everything for getting a gold transcript out of a human: build an assignment,
put it on a machine they can reach, get the finished file back, turn it into a
gold reference.

Design and reasoning live in
[../docs/annotation_environment_design.md](../docs/annotation_environment_design.md)
(the environment) and
[../docs/annotator_image_design.md](../docs/annotator_image_design.md) (the
machine). **Read those before changing anything here** — most of what looks
arbitrary is load-bearing, and the docs say which.

---

## The shape of it

```
              PRIVATE NETWORK                  |         GCP
                                               |
  manifest + media + template                  |
        |                                      |
        |  make_kit.py                         |
        v                                      |
   annotation/kits/<case>/<annotator>/         |
        |                                      |
        |  push_kit.sh  ----------------------> bucket ---> annotator's machine
        |                                      |                  |
        |                                      |            they annotate
        |                                      |                  |
   data/gold/<case>/  <--- pull_submission.py <--- bucket <--- Submit button
        |                                      |
        |  scripts/eaf_to_gold.py              |
        v                                      |
   benchmarks/references/  (tracked, de-identified)
```

The private network can reach out but **cannot be reached in**, and annotators
can't see it from home or campus. That's why the machines are in the cloud, and
why the boundary is crossed outward, by hand, by `push_kit.sh` and
`pull_submission.py`. Roughly 57 MB out per case and a few hundred KB back — a
dozen-odd times for the first campaign. Don't automate it; see the environment
design §2 for why.

---

## Running a campaign

**Once**, build the machine image:

```bash
# from the ELAN download page
export ELAN_DEB_URL=https://www.mpi.nl/tools/elan/ELAN_7-1_linux.deb
export ELAN_DEB_SHA256=01c52cb5cde3090b2e9a46a299936b7a358363e7231507f7fc8a01fba7394073

annotation/gcp/build_image.sh v1
```

`build_image.sh` is safe to re-run: it deletes a leftover builder VM from a
failed attempt before starting, and it checks up front that `annotator-v1`
doesn't already exist rather than discovering that at the last step. A failed
build leaves the builder up on purpose so you can log in and look; the next run
clears it.

`build_image.sh` hands those to `install.sh` by naming them in the remote
command. Exporting them in your own shell is necessary but not sufficient:
`gcloud compute ssh` starts a fresh shell on the builder, and `sudo` resets the
environment again on top of that, so nothing travels on its own. If you run
`install.sh` by hand, put them on the command line —
`sudo ELAN_DEB_URL=... bash install.sh`.

**Once per annotator:**

```bash
export ANNOTATION_BUCKET=gs://som-anno-data-bucket
annotation/gcp/new_vm.sh --annotator jamie
# grant them access (new_vm.sh prints the two commands), then send them
# annotation/gcp/connect.sh and walk them through it once -- 15 minutes
```

**Per case, three commands:**

```bash
conda activate md-speech

python annotation/make_kit.py --case 261473 --annotator jamie
annotation/gcp/push_kit.sh   --case 261473 --annotator jamie
# ... they annotate, then click Submit ...
python annotation/pull_submission.py --case 261473 --annotator jamie
```

**When the campaign ends:** pull every submission first, then
`annotation/gcp/delete_vm.sh --annotator jamie`. Deleting the machine deletes
its disk, which is the point — it removes the copy of real-name data that was
living on it.

### Excerpts

A case can be an excerpt rather than a whole encounter:

```bash
python annotation/make_kit.py --case 261473 --annotator jamie \
    --span-start 120 --span-end 420
```

The window gets marked on the `NOTES` tier so it sits on the timeline rather
than in a README nobody re-reads. `NOTES` is excluded from both `gold.txt` and
`gold.rttm`, so the marker can't reach a published reference.

---

## What each piece does

| | |
|---|---|
| `make_kit.py` | Manifest + template + media → a kit on local disk. The substance: it writes the pre-linked `.eaf`. |
| `submit_checks.py` | Two checks on an annotated `.eaf`. Runs on the machine *and* again locally on the way back in. Standard library only, so the VM needs no packages. |
| `pull_submission.py` | Submission → `data/gold/` → `scripts/eaf_to_gold.py`. |
| `gcp/build_image.sh` | Build `annotator-vN` once. |
| `gcp/new_vm.sh` | One machine per annotator. |
| `gcp/connect.sh` | Handed to the annotator. Opens a private tunnel to their machine. |
| `gcp/push_kit.sh` | Kit → bucket → staged on their machine. |
| `gcp/delete_vm.sh` | Tear down after pulling submissions. |
| `image/install.sh` | Builds the desktop. Runs as root during image build only. |
| `image/desktop/` | The three launchers and the small helpers behind them. |
| `image/elan_prefs/` | The one ELAN setting that isn't a default, plus notes. |

`kits/` is gitignored — it holds real-name media.

---

## The one thing not to break

Every kit's `.eaf` records its media twice, absolute and relative, and **both
are written against `CANONICAL_ROOT` in `make_kit.py`** (`/srv/multidata/case`)
— the path every machine mounts a case at. That is the only reason ELAN opens
without a "locate media" dialog on a machine that isn't the one the file was
made on.

If you change `CANONICAL_ROOT`, you must change where `image/install.sh` creates
the staging directory and where `image/desktop/stage-case` puts kits, or every
kit breaks. `tests/test_make_kit.py` fails loudly if the URLs stop being
canonical.

---

## Status

**Tested for real:** `make_kit.py` (a 57 MB kit built from case 261473 — 18 s,
including the 480p transcode), `submit_checks.py` (against real annotated gold,
where it correctly found two empty segments), `pull_submission.py` (local path),
and 27 unit tests in `tests/test_make_kit.py` and `tests/test_submit_checks.py`.

**Not yet run:** everything touching GCP — `gcp/*.sh` and `image/install.sh`.
They're written and syntax-checked, and no project existed to run them against.
Two things in particular are unverified and will need a real attempt:

- **`ELAN_DEB_URL` has no default in the scripts**, because inventing a
  download URL that 404s is worse than a variable that fails loudly.
  `build_image.sh` checks it locally before booting anything.
  `ELAN_DEB_SHA256` is optional: verified when given, and printed when not, so
  a first build hands you the value to pin for the next one. The working values
  for ELAN 7.1 are in the command above.
- **`image/elan_prefs/` is empty.** ELAN's preferences file format wasn't
  confirmed, so rather than ship an XML file ELAN might silently ignore, the
  directory is empty and `install.sh` warns. `image/elan_prefs/NOTES.md` has
  the one-time step: set autosave by hand in ELAN once, copy the file out,
  commit it.

Then work the acceptance checklist in the image design §6 — playback first,
with a real kit, before anyone is onboarded.
