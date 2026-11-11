# annotation/: the coordinator's runbook

The exact steps, with commands, to take a case from a kit to a gold reference
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
| **processing host** `annotation/kits/<case>/<annotator>/` | every kit as built, and every pass once it's back: the master copies |
| **processing host** `data/gold/<case>/<case>.gold.eaf` | the adjudicated file (written by step 6) |
| **Box** `Audio and Labeling/<annotator's folder>/<case>/<annotator>/` | kits that are out for work. A courier, not an archive |
| **git** `annotation/assignments.csv` | who has which pass, and its status. Update it at every step |
| **git** `benchmarks/references/<case>.gold.*` | the redacted reference. The only annotation output in git |

Kits and `data/gold/` contain real names. They stay on the processing host
and Box: never email them, and never commit them (both are gitignored).

## The steps

`<C>` is the case, `<A>` the pass-1 annotator, `<B>` the pass-2 annotator
(never the same person). `$BOX` is the Box Drive folder
`Audio and Labeling` on your laptop (on a Mac,
`~/Library/CloudStorage/Box-Box/Audio and Labeling`). `$HOST` is the
processing host's ssh name. Commands marked *host* run on the processing
host in `~/projects/multidata-local` with the `md-speech` environment active;
commands marked *laptop* run where Box Drive is.

### 1. Build the pass-1 kit (host)

For an excerpt, add `--span-start S --span-end E` (seconds) to either route.

- **Route A, blind:**
  `python annotation/make_kit.py --case <C> --annotator <A>`
- **Route B, seeded:** three moves.
  1. `python annotation/make_kit.py --case <C> --annotator <A> --seed`
     lists the draft's speakers, with talk time and sample lines. If the case
     has no pipeline transcript yet, this makes one (GPU, about a minute).
     Once a kit uses a seed, it is never regenerated.
  2. Decide each speaker's role (`LEARNER`, `PATIENT`, `PRECEPTOR`,
     `ANNOUNCEMENT`, `OUTSIDE_ROOM`) from the samples, and the video if
     needed. Leave out a speaker you can't place: their speech goes on an
     `UNASSIGNED` tier for the annotator to sort.
  3. Run the same command with the map added:
     `--roles SPEAKER_00=PATIENT,SPEAKER_01=LEARNER,...`

Add a row to `assignments.csv`: pass `1`, the route, status `assigned`, and
in `notes`, why this case was chosen.

### 2. Send it (laptop)

```bash
mkdir -p "$BOX/<A's folder>/<C>"
scp -r $HOST:projects/multidata-local/annotation/kits/<C>/<A> "$BOX/<A's folder>/<C>/"
```

Keep the folder whole: the `.eaf` finds its media through the `media/`
folder beside it. Tell the annotator it's there.

### 3. The annotator works

They save in place (same file name, same folder) and tell you when they're
done. Set status `in_progress` if you want to track that.

### 4. Bring pass 1 back and check it (laptop, then host)

```bash
scp "$BOX/<A's folder>/<C>/<A>/<C>.pass1.eaf" $HOST:projects/multidata-local/annotation/kits/<C>/<A>/
```
```bash
python annotation/submit_checks.py annotation/kits/<C>/<A>/<C>.pass1.eaf
```

If it refuses, send it back with the message. Once it passes, that copy is
**frozen**: nobody edits it again. Set status `returned` and `returned_on`.

### 5. Build and send the pass-2 kit (host, then laptop)

```bash
python annotation/make_pass2_kit.py --case <C> --from <A> --to <B>
```

This copies A's kit to `kits/<C>/<B>/` (same media) with the pass-1 file
renamed `<C>.pass2.eaf`, and writes kit.json and a pass-2 README. A's kit is
left untouched. Send it as in step 2, to `"$BOX/<B's folder>/<C>/"`. Add a
row: pass `2`, same route, `assigned`.

For a **blind** case you may also give B the pipeline's machine transcript
(`data/elan/<C>/`), to point at places worth re-listening. For a **seeded**
case, don't: it comes from the seed's engine, so it adds no independent
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
- adds a row to the manifest's `gold` table.

It refuses to overwrite a different `.gold.eaf` unless you add
`--replace-gold`. Kits made before `make_pass2_kit.py` existed don't name
the pass-1 annotator, so add
`--kit annotation/kits/<C>/<A> --annotator <A>` for those.

**Then read `benchmarks/references/<C>.gold.txt` for names** before
committing. The export redacts the names in the manifest. It warns when a
speaker tier has speech and the manifest has no name for that role. It
misses names heard differently from the record. If a name escaped, mask it
by hand in the `.gold.txt` (replace it with the role's tag, such as
`[LEARNER_NAME]`), and say in the case's `assignments.csv` notes that you
did, without writing the name. A re-export undoes the mask.

Set the pass-2 row's status to `gold`, then commit:

```bash
git add benchmarks/references/<C>.gold.* annotation/assignments.csv
git commit -m "<C> gold"     # never put a name in a commit message
git push
```

### 7. Score it (host)

For each engine (see `python scripts/bench_status.py` for the empty cells):

```bash
python benchmarks/run_benchmark.py --case <C> --audio data/audio/<C>/<camera>.wav \
    --transcript data/transcripts/<C>/<camera>_<engine>_<model>.json --model <model>
```

Or leave out `--transcript` and give `--engine <engine>` to run the engine
now. The excerpt window, if any, is read from the `.gold.json`. Commit
`benchmarks/results/runs.csv`.

When a case is finished, tidy its Box folders away (a `complete/` folder is
fine).

## assignments.csv

One row per pass per annotator.

| Column | Meaning |
|---|---|
| `case_id`, `camera`, `duration_min` | the case, its audio camera, and its video length (an excerpt says so in `notes`) |
| `pass` | `1` = making the transcript, `2` = adjudication |
| `route` | `blind` or `seeded`: how the case's pass 1 started (the same on both of its rows) |
| `annotator` | who has the pass (their kit folder name) |
| `status` | `assigned` → `in_progress` → `returned` (back on the host and checked) → `gold` (pass 2 only: exported). `abandoned` if dropped |
| `assigned_on`, `returned_on` | `YYYY-MM-DD` |
| `file` | the master copy on the processing host |
| `notes` | why the case was chosen; anything unusual. Never a name |

Two pass-1 rows for one case, both blind, are a double annotation for
agreement (methods §2.4).

## The tools

| | |
|---|---|
| `make_kit.py` | Manifest + template + media → a pass-1 kit: the `.eaf` with the template tiers and both media linked, a 480p proxy video, the case WAV, `kit.json` and a README. `--seed`/`--roles` fill the tiers from the seed transcript (route B). |
| `make_pass2_kit.py` | A checked pass 1 → a pass-2 kit for a different person. |
| `submit_checks.py` | Refuses a file with an empty annotation or anything left on `UNASSIGNED`; warns about annotations outside an excerpt window. Standard library only. |
| `../scripts/eaf_to_gold.py` | A returned pass 2 → the gold files and the manifest row (step 6). |
| `../scripts/bench_status.py` | Which cases have gold, their current scores, and which engines are still unscored. |
