# ASR evaluation: methods

How this project builds human reference transcripts ("gold") of recorded
clinical encounters, and how it scores speech-recognition (ASR) engines
against them. It is written as the methods section of a paper, and also as
the starting point for anyone who has to run or continue the work.

**The question.** Does an ASR engine hear what a careful human listener hears
in these rooms? And does its diarizer (the part that decides who spoke when)
cope with overlapping talk, PA announcements and noise?

## Which document to read

Each document has one job. This one gives the whole process and the reasons
for it. The others hold the detail.

| Document | Audience | Holds |
|---|---|---|
| this file | anyone; paper readers | the whole procedure, end to end, and its known limitations |
| [transcription_standards.md](transcription_standards.md) | annotators, reviewers | the conventions: tiers, segmentation, what to type, normalization levels, change control |
| [annotator_guide.md](annotator_guide.md) | annotators | how to do pass 1 and pass 2 in ELAN, step by step |
| [../annotation/README.md](../annotation/README.md) | the coordinator | the exact commands at each step, and the tracker (`assignments.csv`) |
| [asr_provider_spec.md](asr_provider_spec.md) | developers | how engines plug in, and the design for cloud providers |
| [status.md](status.md) | everyone | what exists today, and the open problems |

When this file and the standards disagree, the standards win, and this
file has a bug.

---

## 1. Material

**Encounters.** Simulated encounters in our simulation center. A learner
(medical student) interviews and examines a standardized patient, an actor
playing a case (the scenario). In most encounters a preceptor (supervising clinician) then enters and gives feedback. A
recorded PA system announces each phase (door note, start, time warnings,
end, feedback). Each exam room has two pan/tilt/zoom cameras and one shared
microphone feed.

**Audio.** For each recording, one camera is the *audio camera*. The pipeline
extracts its soundtrack as 16 kHz mono 16-bit WAV
(`data/audio/<recording>/<camera>.wav`). Every engine is scored on that same file,
and its SHA-256 is recorded with every score.

**Sample.** Gold is expensive: a first pass takes 6–10 times real time, so
a 20-minute encounter takes most of a day. Recordings are therefore chosen
deliberately rather than at random, to cover the conditions that break ASR:
quiet against noisy rooms, number of speakers and amount of overlap, learner
accent and speech rate, and microphone. The reason for choosing each recording is
recorded in the `notes` column of `annotation/assignments.csv`. A reference
can be an **excerpt** of an encounter (a time window) rather than all of it.
WER is a rate, so an excerpt is scored only within its window. Results are
reported as estimates over the named sample, never as properties of the
whole corpus.

**Privacy.** The patient is an actor, but learners and preceptors are real
people, and they say their names. Anything holding a real name stays on the
processing host and the project's Box folder, and is never put into git
(§4). The IRB cleared sending audio to cloud ASR vendors for transcription
on 2026-09-10. No recording may be sent until the `recordings.cloud_release` check
exists in code and the recording has been released ([status.md](status.md)).

---

## 2. Making a reference transcript

### 2.1 What an annotator produces

One ELAN file per recording, with a fixed set of tiers taken from the versioned
template `elan/template.etf`:

| Tier | Holds |
|---|---|
| `LEARNER`, `PATIENT`, `PRECEPTOR` | each person's speech |
| `ANNOUNCEMENT` | the PA system |
| `OUTSIDE_ROOM` | intelligible speech from outside the room |
| `NOTES` | free-text observations (noise, equipment trouble); never scored |

Speech is cut into utterances (a breath group, usually a few seconds),
each bounded to about a tenth of a second. Annotators segment one tier at a
time across the whole recording, so overlapping speech is simply two tiers
holding annotations at the same moment. No overlap markup is needed.

The text is **verbatim**: false starts, repetitions, filled pauses (`uh`,
`um`, `hmm`) and backchannels (`mm-hmm`, `uh-huh`, `uh-uh`, `mm-mm`,
`huh?`, and `mm` for "go on") are all typed. A small markup set records prolongation (`so:`),
cut-off words (`par-`), uncertainty (`[unintelligible]`,
`[unintelligible: best guess?]`) and optional vocalizations (`[laugh]`).
Numbers are written as spoken. Names are typed as heard; redaction happens
later, mechanically. The full rules are in
[transcription_standards.md](transcription_standards.md) §4–§6.

### 2.2 Two passes by two people

Every reference is made in two passes, by two different people.

**Pass 1** makes the transcript. It starts in one of two ways:

- **Route A, blind (the default).** The annotator starts from empty tiers
  and the media. They see no machine transcript and no other person's work.
- **Route B, seeded (when annotators are scarce).** The tiers start out
  holding one fixed machine transcript (`whisperx_disfluent`, model
  `large-v3`), cut into utterances and placed on role tiers by a speaker-to-
  role map decided before the kit is built. The annotator corrects every
  line against the audio. Starting from a draft biases the reference toward
  the engine that made it, so route B gold is labelled as seeded everywhere
  it goes. That engine is never scored against it, and seeded results are
  reported apart from blind ones (§3.4).

When pass 1 is returned it is checked (§2.3) and then **frozen**: nobody
edits that file again.

**Pass 2** adjudicates. A second person opens a copy of the frozen pass 1
with the same media, listens to the whole encounter, and corrects words,
boundaries, speakers and markup. Their job is to decide what the audio
contains, not to reconcile two documents. In route A they may also be given
the pipeline's machine transcript, *only* as a pointer to places worth
re-listening; it is never copied from. In route B they get no machine
transcript, because the only one available comes from the same engine as the
seed. The result of pass 2 is the reference.

### 2.3 Checks

At each return (pass 1, then pass 2), `annotation/submit_checks.py`
refuses a file that has an annotation with no text (it would become a
phantom speaker turn), or anything left on the seeded draft's `UNASSIGNED`
tier. For an excerpt, it warns about annotations outside the window. These
are the checks a machine can decide. Everything else is the annotator's
listening, guided by the guide's hand-off checklist.

### 2.4 Agreement between annotators

To estimate how far two careful humans differ, about 20% of recordings should be
annotated twice, blind and independently. The WER between the two pass-1
files is the **noise floor**: no engine can meaningfully do better.
Agreement comes only from blind passes; two corrections of the same seed
agree partly because they started from the same text.

**No recording has been double-annotated yet, so no noise floor exists.** Every
reported number must say so until one does.

---

## 3. From the adjudicated file to the reference files

### 3.1 Export

`scripts/eaf_to_gold.py` turns the adjudicated ELAN file into three files
in `benchmarks/references/`, the only place gold is tracked in git:

| File | Contents | Used for |
|---|---|---|
| `<recording>.gold.txt` | the text of every speech tier (`LEARNER`, `PATIENT`, `PRECEPTOR`, `ANNOUNCEMENT`, `OUTSIDE_ROOM`), in time order, as one line, with names replaced by tags | WER, CER |
| `<recording>.gold.rttm` | the same tiers as speaker turns (same-tier utterances 0.3 s or less apart are merged into one turn), labelled with role names | DER (not computed yet, §3.4) |
| `<recording>.gold.json` | how pass 1 was made (blind or seeded, and if seeded, which engine, model and transcript hash), the standards version, the export date, the excerpt window | refusing biased comparisons; reporting |

`ANNOUNCEMENT` and `OUTSIDE_ROOM` are in both the text and the turns,
because the question is whether an engine hears what a person in the room
hears (standards 1.2). `NOTES` is in neither.

The export also adds a row to the `gold` table in `manifest.sqlite`
(annotator, dates, standards version, span, how pass 1 was made). The table
is append-only, so a re-export adds a row rather than replacing one.

### 3.2 Name redaction

The manifest holds each recording's learner, standardized-patient and preceptor
names. On export, every word of the transcript is compared with each part
of each name. It is redacted if it matches exactly, ignoring letter case, or if it
is at least three letters long and its `difflib` similarity ratio to a name
part is at least 0.78. Adjacent redacted words collapse into one tag, such as
`[LEARNER_NAME]`. The 0.78 threshold catches one-letter misspellings
("Jamey" for "Jamie"). Lowering it to catch more distant spellings also
redacts ordinary words: at 0.75, "then" and "when" match the surname "Shen".

This is a best-effort first line, not a guarantee. Two recordings need a human:

- **No name on record.** If a role tier has speech but the manifest has no
  name for that role (preceptor names are often missing), the export prints a
  warning, and that person's spoken name will not be redacted.
- **A name heard differently from the record**, below the threshold.

So **the coordinator reads every `.gold.txt` before committing it.** A name
that escaped is masked by hand in the `.gold.txt`, the only sanctioned hand
edit, and the fact (never the name) is recorded in that case's
`assignments.csv` row. A re-export undoes the mask, so it must be re-applied.

### 3.3 Where files live

| Layer | Location | In git? | Holds |
|---|---|---|---|
| working copies | `annotation/kits/<recording>/<annotator>/` on the processing host; Box while out for work | no | each pass as built and returned; pass 1 frozen on return |
| adjudicated original | `data/gold/<recording>/<recording>.gold.eaf` | no | the reference with real names |
| publishable reference | `benchmarks/references/<recording>.gold.*` | yes | redacted text, turns, provenance |
| scores | `benchmarks/results/runs.csv` | yes | one row per scored run |
| per-run detail | `benchmarks/runs/<run_id>/` | no | the engine's full output (contains names) |

The processing host is backed up nightly ([operations.md](operations.md)).
Box is only a courier.

---

## 4. Scoring an engine

### 4.1 Producing the hypothesis

`benchmarks/run_benchmark.py` either runs an engine on the recording's WAV or
scores a transcript the pipeline already made. Engine outputs are cached by
(engine, model, audio hash), so re-scoring never re-runs or re-bills an
engine. The engine's words are joined in time order into one string. If the
reference is an excerpt, only words that start inside the window are kept;
the window is read from `<recording>.gold.json`.

Before scoring, the script refuses to score an engine against gold seeded
by that same engine.

### 4.2 Making the two texts comparable

The same steps are applied to the reference and the hypothesis
(`multidata.redact`, `multidata.normalize`):

1. **Redact names** with the manifest names for the recording, as in §3.2. The
   engine heard the real name and wrote it; the reference already has a tag.
   Both end up as a tag, which step 2 removes, so a spoken name counts neither
   for nor against the engine.
2. **L0, verbatim profile:**
   - remove every `[...]` span: name tags, `[unintelligible]`, guesses,
     vocalizations, and any audio-event tags an engine emits;
   - lowercase;
   - remove `. , ! ? ; " ' ( )`;
   - drop tokens with no letter or digit left (a free-standing dash typed as a
     pause mark);
   - map unambiguous spelling variants of backchannels and hums to one form
     (`mhm`, `mmhmm`, `mmm-hmm` → `mm-hmm`; `uhhuh` → `uh-huh`; `hmmm` →
     `hmm`; `mmm` → `mm`), and `ok` (`OK`, `O.K.`) to `okay`. A run-together
     `uhuh` is left alone: it could be yes or no;
   - take the annotators' markup off words: the prolongation colon (`so:` →
     `so`) and the cut-off hyphen (`I-` → `I`). No engine writes these marks,
     so an engine that wrote the word gets credit, and one that left out a
     spoken word still loses it;
   - collapse whitespace.

   Hyphens inside words (`mm-hmm`, `follow-up`) are kept.
3. **L1, filler-neutral profile:** L0, minus the filled pauses `uh`, `um`
   and `hmm`, and minus cut-off words entirely (`I- I think` → `i think`).
   Backchannels are never removed at any level: `uh-huh` (yes) and `uh-uh`
   (no) carry the patient's answer.

### 4.3 Metrics

| Metric | Profile | Ledger column | Asks |
|---|---|---|---|
| WER | L0 | `wer` | did it hear every word spoken, restarts and fillers included? |
| WER | L1 | `wer_l1` | did it hear every word that carries meaning? |
| CER | L0 | `cer` | the same, by character |
| DER, JER | gold turns | not yet | did its diarizer get who spoke when, through overlap and noise? |

WER and CER are computed with `jiwer`. Engines that never write filled
pauses lose at L0 for a formatting policy rather than for what they heard;
L1 is the fair comparison for them.

Each run appends one row to `benchmarks/results/runs.csv`. It records the
engine, model, audio hash, the three scores, how the reference's pass 1 was
made (`gold_pass1`), the scoring method (`scoring`), the hash of the
reference file (`gold_sha256`), the git commit and the machine. The full
output and configuration go to `benchmarks/runs/<run_id>/`.

### 4.4 Which numbers can be compared

Two scores are comparable only if they used the same scoring method and the
same reference file. `scripts/bench_status.py` shows only rows whose
`scoring` matches the current method and whose `gold_sha256` matches the
reference as it is now. Older rows stay in the ledger as history.

### 4.5 Reporting rules

- Report **per recording**, not only means: with a handful of recordings, a mean hides
  everything.
- Report results against **blind and seeded gold separately**, never
  averaged together. Engine rankings rest on blind gold.
- Name the sample's strata, and whether any recording is an excerpt.
- State the noise floor, or that none was established (§2.4).
- For cloud engines, record the model version the vendor reports. Vendors
  update models silently, so a cloud number is a snapshot.

---

## 5. Known limitations

Current as of 2026-11-11. When one is fixed, delete it here.

- **No diarization score yet.** Gold turns exist (with overlap), but DER and
  JER are not computed, so the second half of the question is open.
- **No noise floor.** No recording has two independent blind passes (§2.4).
- **Numbers are not canonicalized.** Gold writes "one twenty over eighty";
  an engine writing "120/80" is counted wrong.
- **Spelling variants of ordinary words count as errors.** For example
  `kinda`/`kind of`, `follow-up`/`follow up`. Only backchannel and hum
  spellings, and `ok`/`okay`, are unified.
- **A true partial word still counts.** `th-` becomes `th` at L0, which no
  engine writes. Rare in practice: most cut-offs in our gold are whole words.
- **Uncertain spans still cost the engine.** Removing `[unintelligible]`
  from the reference leaves nothing there, but the engine still writes its
  guess, which counts as inserted words. Fixing this needs the engine's words
  in those time windows dropped, using word timestamps; not built.
- **Name redaction can miss** (§3.2). A missed name in the hypothesis
  costs the engine about one word per mention.
- **Preceptor names are mostly not in the manifest**, so preceptors'
  spoken names are not redacted automatically. 261498's reference contains
  one ([status.md](status.md)).
- **Audio clock on room 11's camera 16.** In three recordings the WAV runs ahead
  of the video by up to 15 s ([status.md](status.md)). WER is unaffected (no
  timing involved), and so is DER, as long as gold and engine both use the
  WAV. Comparisons with video or pose are affected.
