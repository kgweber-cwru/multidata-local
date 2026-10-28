# annotation/

How a hand transcript gets made, from kit to gold. The rules it follows
(blind pass 1, then adjudication) are in
[transcription_standards.md](../docs/transcription_standards.md) §9; this is
the logistics.

## Where things live

| Place | Holds | Role |
|---|---|---|
| **tofino** `annotation/kits/<case>/<annotator>/` | every kit as built, and every finished pass once it's back | the master copy, backed up nightly |
| **tofino** `data/gold/<case>/` | `<case>.gold.eaf`, the adjudicated file `eaf_to_gold.py` exports | gold only |
| **Box** `Audio and Labeling/<annotator folder>/<case>.pass<N>/` | kits that are out for work, including Kate's own | the courier, and where work in progress lives |
| **laptop** | Box Drive, ELAN, the git checkout | where Kate works; holds nothing that isn't also in Box or on tofino |
| **git** `annotation/assignments.csv` | who has which pass, and where it is | the tracker -- edit it at every step |

Everything with real names (kits, `data/gold/`) is gitignored and stays on
tofino and Box. Only the de-identified export in `benchmarks/references/`
goes into git.

## The steps

`<C>` is the case, `<A>` the pass-1 annotator, `<B>` the pass-2 annotator
(never the same person), `$BOX` is
`~/Library/CloudStorage/Box-Box/Audio and Labeling`.

1. **Build the kit** (tofino, md-speech):
   `python annotation/make_kit.py --case <C> --annotator <A>`
   Add `--span-start/--span-end` for an excerpt.

2. **Send it** (laptop):
   `scp -r tofino:projects/multidata-local/annotation/kits/<C>/<A> "$BOX/<A's folder>/<C>.pass1"`
   Keep the folder whole -- the `.eaf` finds its media through the `media/`
   folder beside it. Add a row to `assignments.csv`: `assigned`.

3. **Work.** The annotator saves in place, same file name, same folder, and
   tells Kate when they're done.

4. **Bring pass 1 back** (laptop):
   `scp "$BOX/<A's folder>/<C>.pass1/<C>.pass1.eaf" tofino:projects/multidata-local/annotation/kits/<C>/<A>/`
   then on tofino, `python annotation/submit_checks.py annotation/kits/<C>/<A>/<C>.pass1.eaf`.
   From now on that copy is frozen: nobody edits it. `returned`.

5. **Make the pass-2 kit** (tofino) -- the same kit with the pass-1 file
   renamed:
   ```bash
   cd annotation/kits/<C>
   cp -r <A> <B> && rm -f <B>/*.pfsx <B>/*.eaf.0*
   mv <B>/<C>.pass1.eaf <B>/<C>.pass2.eaf
   ```
   Send it as in step 2, to `"$BOX/<B's folder>/<C>.pass2"`. New row:
   pass 2, `assigned`. If B is Kate, her Box folder is `kate`.

6. **Bring pass 2 back** as in step 4 (`<C>.pass2.eaf`, into
   `kits/<C>/<B>/`), check it, then make it gold (tofino):
   ```bash
   mkdir -p data/gold/<C>
   cp annotation/kits/<C>/<B>/<C>.pass2.eaf data/gold/<C>/<C>.gold.eaf
   python scripts/eaf_to_gold.py --eaf data/gold/<C>/<C>.gold.eaf --case <C> \
       --annotator <A> --gold-notes "pass 2: <B>"     # + --span-start/--span-end if an excerpt
   git add benchmarks/references/<C>.gold.* && git commit && git push
   ```
   `gold`.

When something is done, tidy its Box folder away (a `complete/` folder is
fine). Box is a courier, not an archive -- tofino has the copy that counts.

## assignments.csv

One row per pass per annotator.

| Column | Meaning |
|---|---|
| `pass` | `1` = blind, `2` = adjudication |
| `status` | `assigned` → `in_progress` → `returned` (on tofino, checked) → `gold` (pass 2 only: exported) |
| `duration_min` | length of the case's video. An excerpt says so in `notes` |
| `assigned_on`, `returned_on` | `YYYY-MM-DD` |
| `file` | where the master copy is, on tofino |

Two pass-1 rows for one case is a double annotation (standards §10).

## The tools

| | |
|---|---|
| `make_kit.py` | Manifest + template + media → a kit: the `.eaf` with the six template tiers and both media files linked, a 480p proxy video, the case wav, `kit.json` and a README. |
| `submit_checks.py` | Refuses a file with an empty segment; warns about annotations outside an excerpt window. Standard library only. |
| `pull_submission.py` | Older all-in-one return script. It exports a pass 1 straight to gold, skipping pass 2 -- which is how the first three gold cases ended up unadjudicated -- so don't use it. |
