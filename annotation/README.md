# annotation/: the coordinator's runbook

The exact steps, with commands, to take a recording from a kit to a gold reference
and its scores. For what each step is for, read
[../docs/asr_evaluation_methods.md](../docs/asr_evaluation_methods.md) first.
For what annotators do, see [../docs/annotator_guide.md](../docs/annotator_guide.md).

Every gold reference is two passes by two different people. Pass 1 makes
the transcript, either **blind** (route A, the default: empty tiers) or
**seeded** (route B, when annotators are scarce: a fixed machine transcript
to correct). Pass 2 adjudicates it.

## Where things live

| Place | Holds |
|---|---|
| **processing host** `annotation/kits/<recording>/<annotator>/` | every kit as built, and every pass once it's back: the master copies |
| **processing host** `data/gold/<recording>/<recording>.gold.eaf` | the adjudicated file (written by step 6) |
| **Box** `Audio and Labeling/<annotator's folder>/<recording>/<annotator>/` | kits that are out for work. A courier, not an archive |
| **processing host** `manifest.sqlite`, table `assignments` | who has which pass, and its status. The scripts below keep it up to date; `assignments.py` does the rest |
| **git** `db_seed/assignments.csv` | a backup of that table, rewritten whenever a script changes it. Never edit it by hand |
| **git** `benchmarks/references/<recording>.gold.*` | the redacted reference. The only annotation output in git |

Kits and `data/gold/` contain real names. They stay on the processing host,
Box and `~/Documents/transcription` on the laptop: never email them, and
never commit them (both are gitignored).

## The steps

`<C>` is the recording, `<A>` the pass-1 annotator, `<B>` the pass-2 annotator
(never the same person). `$BOX` is the Box Drive folder
`Audio_and_Labeling` on your laptop (on a Mac,
`Users/[username]/Library/CloudStorage/Box-Box/Audio_and_Labeling`). `$HOST` is the
processing host's ssh name. Commands marked *host* run on the processing
host in `~/projects/multidata-local` with the `md-speech` environment active;
commands marked *laptop* run where Box Drive is.

### 1. Build the pass-1 kit (host)

For an excerpt, add `--span-start S --span-end E` (seconds) to either route.

- **Route A, blind:**
  `python annotation/make_kit.py --recording <C> --annotator <A>`
- **Route B, seeded:** three moves.
  1. `python annotation/make_kit.py --recording <C> --annotator <A> --seed`
     lists the draft's speakers, with talk time and sample lines. If the recording
     has no pipeline transcript yet, this makes one (GPU, about a minute).
     Once a kit uses a seed, it is never regenerated.
  2. Decide each speaker's role (`LEARNER`, `PATIENT`, `PRECEPTOR`,
     `ANNOUNCEMENT`, `OUTSIDE_ROOM`) from the samples, and the video if
     needed. Leave out a speaker you can't place: their speech goes on an
     `UNASSIGNED` tier for the annotator to sort.
  3. Run the same command with the map added:
     `--roles SPEAKER_00=PATIENT,SPEAKER_01=LEARNER,...`

Add `--notes "why this recording was chosen"` to the command that builds the
kit. It adds the pass-1 row (route, `assigned`, today's date).

### 2. Send it (laptop)

```bash
mkdir -p "$BOX/<A's folder>/<C>"
scp -r $HOST:projects/multidata-local/annotation/kits/<C>/<A> "$BOX/<A's folder>/<C>/"
```

Keep the folder whole: the `.eaf` finds its media through the `media/`
folder beside it. Tell the annotator it's there.

### 3. The annotator works

They save in place (same file name, same folder) and tell you when they're
done. To track that they've started:
`python annotation/assignments.py set <C> <A> --status in_progress`.

### 4. Bring pass 1 back and check it (laptop, then host)

```bash
scp "$BOX/<A's folder>/<C>/<A>/<C>.pass1.eaf" $HOST:projects/multidata-local/annotation/kits/<C>/<A>/
```
```bash
python annotation/assignments.py return <C> <A>
```

That runs `submit_checks.py` on the file. If it refuses, send it back with the
message. Once it passes, the row is `returned` with today's date, and that
copy is **frozen**: nobody edits it again.

### 5. Build and send the pass-2 kit (host, then laptop)

```bash
python annotation/make_pass2_kit.py --recording <C> --from <A> --to <B>
```

This copies A's kit to `kits/<C>/<B>/` (same media) with the pass-1 file
renamed `<C>.pass2.eaf`, and writes kit.json and a pass-2 README. A's kit is
left untouched. It adds B's pass-2 row (and marks A's pass 1 returned, if
step 4 was skipped). Send it as in step 2, to `"$BOX/<B's folder>/<C>/"`.

If you fix A's pass 1 after the pass-2 kit is built, rebuild it with
`--overwrite`. This replaces B's kit on the host, so do it before B starts,
and send the new kit. It refuses once pass 2 is returned or gold.

For a **blind** recording you may also give B the pipeline's machine transcript
(`data/elan/<C>/`), to point at places worth re-listening. For a **seeded**
recording, don't: it comes from the seed's engine, so it adds no independent
opinion.

### 6. Bring pass 2 back and make it gold (laptop, then host)

```bash
scp "$BOX/<B's folder>/<C>/<B>/<C>.pass2.eaf" $HOST:projects/multidata-local/annotation/kits/<C>/<B>/
```
```bash
python scripts/eaf_to_gold.py --from-kit annotation/kits/<C>/<B>
```

That one command:
- checks the file;
- copies it to `data/gold/<C>/<C>.gold.eaf`;
- writes `benchmarks/references/<C>.gold.txt`, `.gold.rttm` and `.gold.json`
  with names redacted;
- adds a row to the manifest's `gold` table;
- sets B's assignment row to `gold`.

It refuses to overwrite a different `.gold.eaf` unless you add
`--replace-gold`. Kits made before `make_pass2_kit.py` existed don't name
the pass-1 annotator, so add
`--kit annotation/kits/<C>/<A> --annotator <A>` for those.

**Then read `benchmarks/references/<C>.gold.txt` for names** before
committing. The export redacts the names in the manifest. It warns when a
speaker tier has speech and the manifest has no name for that role. It
misses names heard differently from the record. If a name escaped, mask it
by hand in the `.gold.txt` (replace it with the role's tag, such as
`[LEARNER_NAME]`), and record that you did, without writing the name:
`python annotation/assignments.py note <C> <B> "A misheard surname masked by hand in the .gold.txt."`
A re-export undoes the mask.

Then commit:

```bash
git add benchmarks/references/<C>.gold.* db_seed/assignments.csv
git commit -m "<C> gold"     # never put a name in a commit message
git push
```

### 7. Score it (host)

For each engine (see `python scripts/bench_status.py` for the empty cells):

```bash
python benchmarks/run_benchmark.py --recording <C> --audio data/audio/<C>/<camera>.wav \
    --transcript data/transcripts/<C>/<camera>_<engine>_<model>.json --model <model>
```

Or leave out `--transcript` and give `--engine <engine>` to run the engine
now. The excerpt window, if any, is read from the `.gold.json`. Commit
`benchmarks/results/runs.csv`.

When a recording is finished, tidy its Box folders away (a `complete/` folder is
fine).

## Syncing all kits with the laptop (laptop)

```bash
# host -> laptop
rsync -av --update $USER@$HOST:projects/multidata-local/annotation/kits/ ~/Documents/transcription/
# laptop -> host
rsync -av --update ~/Documents/transcription/ $USER@$HOST:projects/multidata-local/annotation/kits/
```

Add `-n` first to see what would change. Use `--update` to stop an older
copy overwriting a newer one.

## The assignments table

One row per pass per annotator, in the manifest (`manifest.sqlite`). A row is
keyed by recording and annotator, like the kit folder
`annotation/kits/<recording>/<annotator>/`, whose `<recording>.pass<N>.eaf` is
the master copy.

| Column | Meaning |
|---|---|
| `recording_id`, `annotator` | the recording, and who has the pass (their kit folder name) |
| `pass` | `1` = making the transcript, `2` = adjudication |
| `route` | `blind` or `seeded`: how the recording's pass 1 started (the same on both of its rows) |
| `camera` | the audio camera the kit was built from |
| `status` | `assigned` → `in_progress` → `returned` (back on the host and checked) → `gold` (pass 2 only: exported). `abandoned` if dropped |
| `assigned_on`, `returned_on` | `YYYY-MM-DD` |
| `notes` | why the recording was chosen; anything unusual. Never a name |

Two pass-1 rows for one recording, both blind, are a double annotation for
agreement (methods §2.4).

```bash
python annotation/assignments.py list                 # the table, with each video's length
python annotation/assignments.py check                # anything that disagrees with the kits and gold
python annotation/assignments.py note <C> <A> "..."   # append to notes
python annotation/assignments.py set <C> <A> --status abandoned   # any other change
python annotation/assignments.py add <C> <A> --pass 1 --route blind   # a row no script made
```

Run `check` after moving files around. It reports kit folders with no row,
rows with no file, returned files that fail `submit_checks.py` or changed
after they were returned, routes or cameras that disagree with `kit.json`,
a pass 2 with no returned pass 1, and gold rows without their gold file or
gold-table row (or the reverse).

Every change rewrites `db_seed/assignments.csv`; commit it with the work it
records. If the table is ever lost, `python annotation/assignments.py import`
loads that file back into an empty table.

## The tools

| | |
|---|---|
| `make_kit.py` | Manifest + template + media → a pass-1 kit: the `.eaf` with the template tiers and both media linked, a 480p proxy video, the recording WAV, `kit.json` and a README. `--seed`/`--roles` fill the tiers from the seed transcript (route B). |
| `make_pass2_kit.py` | A checked pass 1 → a pass-2 kit for a different person. |
| `assignments.py` | The assignments table: list, return, note, set, add, check, import/export. |
| `submit_checks.py` | Refuses a file with an empty annotation or anything left on `UNASSIGNED`; warns about annotations outside an excerpt window. Standard library only. |
| `../scripts/eaf_to_gold.py` | A returned pass 2 → the gold files, the gold-table row, and the assignment set to `gold` (step 6). |
| `../scripts/bench_status.py` | Which recordings have gold, their current scores, and which engines are still unscored. |
