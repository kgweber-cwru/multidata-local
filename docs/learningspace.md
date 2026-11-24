# LearningSpace: where our recordings come from

LearningSpace is the sim center's scheduling, recording and assessment
system. We take two things from it: the **front end's export** (an Excel
workbook, one sheet per recording), and the **Data API**, a read-only
reporting interface described in
[learningspace_api.yaml](learningspace_api.yaml). The client is
`multidata.learningspace`; the backfill is `scripts/ls_backfill.py`.

## Their words and ours

Our names follow LearningSpace's (renamed 2026-11-11; the manifest renames
itself the first time it is opened).

| LearningSpace | Example | Manifest |
|---|---|---|
| **Activity**: an exam series | "CSE 2B Short Station (2026)" | `recordings.activity_title`; GUID `ls_activity_id` |
| **Event**: one scheduled session of an activity | "Standard Exam Wednesday", 26 Feb 2026 | `ls_event_id` |
| **Case**: the scenario | "CSE 2B Case 4 Short Station Wellness (2026)" | `recordings.case_title`; GUID `ls_case_id` |
| **Video recording**: one learner, one case, one room | 261505; GUID `32693b92…` | a `recordings` row: `recording_id` is the export's number (the GUID for a roster row), `ls_recording_id` the GUID |
| **Video file**: one camera's file of a recording | `20260226-162204-v309407-16.mp4` | a `videos` row: `file_number` (`v309407`), `ls_file_id` |
| **Room** | GUID only in the API | `recordings.room_number`; `ls_rooms` maps GUID to number |
| **Learner group** | "CWRU SoM Class of 2028" | `recordings.learner_group` |
| **Student / learner** | | `learner_name`; `ls_learner_id` |
| **Observer**: the faculty member who submits the observation checklist | | `preceptor_name` (the observer *is* the preceptor); GUID `ls_preceptor_id` |

`recording_id`, the six-digit number on every folder, kit and gold file, is
the front end's identifier for a video recording. The API never returns it;
the API identifies a recording by its GUID. So intake needs both: the export
for the number, the API for the rest. Both are stored side by side. If the
export ever goes away, the GUID can become the key without breaking a link.

Files written before the rename still say `case_id` (kit.json, `.gold.json`,
older ledgers); readers accept either (`models.upgrade_keys`). The fixed
kit media path `/srv/multidata/case/<id>` is inside existing kits and stays.

## How a recording is linked

`ls_backfill.py` takes each manifest recording and finds it in the API:

1. activity by title, case by title, learner by name: each must match
   exactly one row, ignoring case and spacing, or nothing is written;
2. the activity's events on the recording's local date;
3. that learner's recordings for each event and case, keeping those that
   start within two minutes of our start time (the export truncates it to
   the minute). This endpoint ignores the event: it returns the learner's
   recordings of that case from every event, each stamped with the event
   asked for. So when two events share the day, the recording's event comes
   from the events' file lists instead;
4. if more than one is left, the one whose files include all of ours (one
   learner can have two recordings starting in the same second, each with
   its own files);
5. the recording's files, matched to our `videos` rows **by filename**;
6. the learner's responses for the case: only the faculty-observation part
   is kept, as points per section and submission in `recording_scores`.
   Its submitter is the preceptor.

It only fills empty fields. A different existing value is a conflict: it is
printed and left alone. The run is a dry run unless `--apply` is given.

**Partly linked** recordings are those made outside any scheduled event (the
2025 remediation sessions, for example): the API reaches recordings only
through events, so they get the activity, case, learner, preceptor and score,
but no recording GUID or file links.

## Running it

```bash
sqlite3 manifest.sqlite ".backup data/backups/manifest_$(date +%F).sqlite"
python scripts/ls_backfill.py                 # dry run, every recording with video
python scripts/ls_backfill.py --apply         # write
python scripts/ls_backfill.py --recording 261505   # just one
```

`LS_API_KEY` goes in `.env` (gitignored). The key is generated in
LearningSpace under System > Security, with Data API access switched on.

Requests are spaced a second apart (`--pace`) and every reply is cached in
`data/ls_api/cache/`, so a dry run followed by `--apply` asks the server
once; `--refresh` ignores the cache. A run over 57 recordings is about 200
requests. Cached replies contain real names, which is why they live under
`data/`.

`--prune` drops recordings that have no video, no LearningSpace link and
nothing else pointing at them; re-importing the export brings them back.

## The roster: every recording, downloaded or not

The manifest also lists every LearningSpace recording since 2024-07-01,
whether or not we have its video, so it can answer how many encounters,
minutes and preceptors there are. These **roster rows** come from the API
alone (`scripts/ls_roster.py`, below) and differ from exported ones:

- **Keyed by GUID.** With no export, there is no six-digit number:
  `recording_id` is the GUID, the same as `ls_recording_id`
  (`Recording.is_roster`). `ls_recording_id` is unique across rows.
- **No names but the SP's.** Learner and preceptor are GUIDs only
  (`ls_learner_id`, `ls_preceptor_id`): most of these people are not in the
  study, and `manifest.upsert_ls_recording` refuses their name columns. The
  API has no SP GUID, so `sp_name` is stored (SPs have consented): it is the
  submitter of the checklist part named "SP", set when exactly one person
  submitted it. Activities without an SP checklist (CSE 2B, for one) leave
  it empty.
- **Duration from LearningSpace.** `duration_s` is the longest camera file's
  `LengthSeconds`, `n_files` the number of files. Exported recordings get
  them too.
- **Hidden from the pipeline.** `manifest.all_recordings()` leaves them out
  (file matching and ingest need the export number); pass `roster=True` for
  counts.
- **Absorbed on export.** When `ls_backfill.py` links an exported recording
  whose GUID already has a roster row, the roster row's values move to the
  numbered row and the roster row goes (`manifest.absorb_roster_row`).
  Backfill only visits recordings with video, so an exported recording not
  yet ingested sits beside its roster row until then; `--prune` removes it.

How it finds them: each event's file list
(`/student-video-recordings/nbome/event/{id}`, which despite its name serves
any event) gives every recording in the event with its files' lengths, in
one call. Each activity's checklist responses then give its case × learner
pairs and the faculty observer, and one lookup per pair ties recordings to
case, learner and preceptor. That lookup also finds the occasional
recording that no event lists; it joins the roster without an event.

```bash
sqlite3 manifest.sqlite ".backup data/backups/manifest_$(date +%F).sqlite"
python scripts/ls_roster.py --estimate        # request count, then stop
python scripts/ls_roster.py --activity "CSE 2B" --limit 20   # a small dry run
python scripts/ls_roster.py                   # dry run, everything since 2024-07-01
python scripts/ls_roster.py --apply           # write
python scripts/ls_roster.py --no-link         # minutes only: no case, learner or preceptor
```

The full run is about 5,300 requests, an hour and a half at the default
pace (`--no-link` about 470). `--since`, `--pace`, `--refresh` and
`--manifest` work as in the backfill; names are never printed.

```sql
-- encounters, hours and preceptors since 2024-07-01
SELECT count(*), round(sum(duration_s) / 3600, 1),
       count(DISTINCT nullif(ls_preceptor_id, ''))
FROM recordings WHERE ls_recording_id != '';
```

**State (2026-11-24, 11,059 requests):** 9,249 recordings in 32
activities, 4,278 hours. 9,130 are tied to a case and learner (119 are
not); 533 came only through the pair lookup (no event lists them); about
100 are listed by two events and keep the first. A preceptor is set on
4,452 and an SP on 4,671: the others' activities have no faculty or no SP
checklist, or more than one submitter. 7 have no length.

## What we take, and what we leave

Taken: identifiers, exact start time (UTC), room, file facts and download
links, the observer's name and checklist points. Left in LearningSpace: item
answers, the learner's own quizzes and self-evaluation, and free-text
comments. The SP's name comes from the "SP" checklist part's submitter
(roster), where an activity has one; the export's SP field has so far always
been `---`, its "none recorded", which import stores as empty. SPs introduce
themselves by their case name, so `sp_name` is not expected to need
redaction in transcripts; keep it out of shared metadata.

## Known gaps (2026-11-11)

- The 4 remediation recordings from April 2025 are partly linked (above).
- 8 of 114 video files have no file link: they belong to those 4.
- 1 recording (261573) has no observation checklist, so no preceptor or score.
- 1 recording (260656) was made outside its day's event (no event lists it), so it has no `ls_event_id`.
- Download links are stored but not yet used: ingest still copies files by hand.
