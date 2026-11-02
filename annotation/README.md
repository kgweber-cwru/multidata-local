# annotation/

How a hand transcript gets made, from kit to gold. Every gold reference is
two passes by two different people: pass 1 makes the transcript, pass 2
adjudicates it. Pass 1 takes one of two routes
([transcription_standards.md](../docs/transcription_standards.md) §9, which
has the rules and the reasons):

- **Route A, blind** (the default): pass 1 starts from empty tiers.
- **Route B, draft-seeded** (when annotators are scarce): pass 1 starts from a
  fixed machine transcript (`whisperx_disfluent` / `large-v3`) already on
  the tiers, and corrects it. Gold made this way is recorded as seeded, and
  the benchmark never scores the seed engine against it.

Only step 1 and the hand-in check differ between the routes; this is the
logistics for both.

## Where things live

| Place | Holds | Role |
|---|---|---|
| **processing host** `annotation/kits/<case>/<annotator>/` | every kit as built, and every finished pass once it's back | the master copy, backed up nightly |
| **processing host** `data/gold/<case>/` | `<case>.gold.eaf`, the adjudicated file `eaf_to_gold.py` exports | gold only |
| **Box** `Audio and Labeling/<annotator folder>/<case>/<annotator>/` | kits that are out for work, including Kate's own | the courier, and where work in progress lives |
| **laptop** | Box Drive, ELAN, the git checkout | where Kate works; holds nothing that isn't also in Box or on the processing host |
| **git** `annotation/assignments.csv` | who has which pass, and where it is | the tracker -- edit it at every step |

Everything with real names (kits, `data/gold/`) is gitignored and stays on
the processing host and Box. Only the de-identified export in `benchmarks/references/`
goes into git.

## The steps

`<C>` is the case, `<A>` the pass-1 annotator, `<B>` the pass-2 annotator
(never the same person), `$BOX` is
`~/Library/CloudStorage/Box-Box/Audio and Labeling`, and `$HOST` is the
processing host's ssh name (during the pilot, `tofino`).

1. **Build the kit** (processing host, md-speech). Add `--span-start/--span-end` for
   an excerpt, either route.
   - **Route A:** `python annotation/make_kit.py --case <C> --annotator <A>`
   - **Route B:** three moves.
     1. `python annotation/make_kit.py --case <C> --annotator <A> --seed`
        lists the draft's speakers: talk time and sample lines. The seed is
        the case's pipeline transcript (the asr stage's engine is the seed
        engine); if the case has none yet, this makes it (GPU, about a
        minute). Once a kit uses it, it is never regenerated.
     2. Decide each speaker's role (`LEARNER`, `PATIENT`, `PRECEPTOR`,
        `ANNOUNCEMENT`, `OUTSIDE_ROOM`) from the samples, the video if
        needed. Leave out a speaker you can't place: their speech goes on an
        `UNASSIGNED` tier for the annotator to sort.
     3. Build it: add `--roles SPEAKER_00=PATIENT,SPEAKER_01=LEARNER,...` to
        the same command. kit.json and the `.eaf` record the seed and the
        role map.

2. **Send it** (laptop):
   `mkdir -p "$BOX/<A's folder>/<C>" && scp -r $HOST:projects/multidata-local/annotation/kits/<C>/<A> "$BOX/<A's folder>/<C>/"`
   Box then mirrors the host: `<folder>/<C>/<A>/` is `kits/<C>/<A>/`, and the
   file name says which pass it is. Keep the folder whole -- the `.eaf` finds its media through the `media/`
   folder beside it. Add a row to `assignments.csv`: `assigned`.

3. **Work.** The annotator saves in place, same file name, same folder, and
   tells Kate when they're done.

4. **Bring pass 1 back** (laptop):
   `scp "$BOX/<A's folder>/<C>/<A>/<C>.pass1.eaf" $HOST:projects/multidata-local/annotation/kits/<C>/<A>/`
   then on the host, `python annotation/submit_checks.py annotation/kits/<C>/<A>/<C>.pass1.eaf`.
   It refuses empty segments and, in a seeded file, anything left on
   `UNASSIGNED`. From now on that copy is frozen: nobody edits it. `returned`.

5. **Make the pass-2 kit** (processing host) -- A's kit, same media, with the
   returned pass-1 file renamed (`make_kit.py` only builds pass-1 kits):
   ```bash
   cd annotation/kits/<C>
   cp -r <A> <B> && rm -f <B>/*.pfsx <B>/*.eaf.0*
   mv <B>/<C>.pass1.eaf <B>/<C>.pass2.eaf
   sed -i 's/"annotator": "<A>"/"annotator": "<B>"/; s/<C>.pass1.eaf/<C>.pass2.eaf/' <B>/kit.json
   ```
   Then edit `<B>/README.txt` to say it's pass 2. Send it as in step 2, to
   `"$BOX/<B's folder>/<C>/"`. New row: pass 2, `assigned`. If B is Kate,
   her Box folder is `kate`. For a seeded case, B works from pass 1 and the
   media only: the pipeline's draft (`data/elan/<C>/`) comes from the same
   engine as the seed, so it adds no independent second opinion.

6. **Bring pass 2 back** as in step 4 (`<C>.pass2.eaf`, into
   `kits/<C>/<B>/`), check it, then make it gold (processing host):
   ```bash
   mkdir -p data/gold/<C>
   cp annotation/kits/<C>/<B>/<C>.pass2.eaf data/gold/<C>/<C>.gold.eaf
   python scripts/eaf_to_gold.py --eaf data/gold/<C>/<C>.gold.eaf --case <C> \
       --kit annotation/kits/<C>/<A> \
       --annotator <A> --gold-notes "pass 2: <B>"     # + --span-start/--span-end if an excerpt
   git add benchmarks/references/<C>.gold.* && git commit && git push
   ```
   `gold`.

When something is done, tidy its Box folder away (a `complete/` folder is
fine). Box is a courier, not an archive -- the processing host has the copy that counts.

## assignments.csv

One row per pass per annotator.

| Column | Meaning |
|---|---|
| `pass` | `1` = making the transcript, `2` = adjudication |
| `route` | `blind` or `seeded`: how the case's pass 1 started (same on both of its rows) |
| `status` | `assigned` → `in_progress` → `returned` (on the host, checked) → `gold` (pass 2 only: exported) |
| `duration_min` | length of the case's video. An excerpt says so in `notes` |
| `assigned_on`, `returned_on` | `YYYY-MM-DD` |
| `file` | where the master copy is, on the processing host |

Two pass-1 rows for one case is a double annotation (standards §10).

## The tools

| | |
|---|---|
| `make_kit.py` | Manifest + template + media → a kit: the `.eaf` with the six template tiers and both media files linked, a 480p proxy video, the case wav, `kit.json` and a README. `--seed`/`--roles` pre-fill the tiers from the seed transcript (route B; `multidata.seeding`). |
| `submit_checks.py` | Refuses a file with an empty segment or anything left on `UNASSIGNED`; warns about annotations outside an excerpt window. Standard library only. |
| `pull_submission.py` | Older all-in-one return script. It exports a pass 1 straight to gold, skipping pass 2 -- which is how the first three gold cases ended up unadjudicated -- so don't use it. |
