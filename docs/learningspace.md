# LearningSpace: where our recordings come from

LearningSpace is the sim center's scheduling, recording and assessment
system. We take two things from it: the **front end's export** (an Excel
workbook, one sheet per recording), and the **Data API**, a read-only
reporting interface described in
[learningspace_api.yaml](learningspace_api.yaml). The client is
`multidata.learningspace`; the backfill is `scripts/ls_backfill.py`.

## Their words and ours

| LearningSpace | Example | In our manifest today |
|---|---|---|
| **Activity** | "CSE 2B Short Station (2026)" | `cases.event_name`; GUID `ls_activity_id` |
| **Event**: one scheduled session of an activity | "Standard Exam Wednesday", 26 Feb 2026 | GUID `ls_event_id` |
| **Case**: the scenario | "CSE 2B Case 4 Short Station Wellness (2026)" | `cases.case_name`; GUID `ls_case_guid` |
| **Video recording**: one learner, one case, one room | front-end number 261505; GUID `32693b92…` | a `cases` row: `case_id` is the front-end number, `ls_recording_guid` the GUID |
| **Video file**: one camera's file of a recording | `20260226-162204-v309407-16.mp4` | a `videos` row; `ls_file_id` |
| **Room** | GUID only in the API | `cases.room_number`; `ls_rooms` maps GUID to number |
| **Student / learner** | | `learner_name`; `ls_learner_id` |
| **Observer**: the faculty member who submits the observation checklist | | `preceptor_name` (the observer *is* the preceptor) |

The six-digit number we call `case_id` is the front end's identifier for a
video recording. The API never returns it; the API identifies a recording by
its GUID. So intake needs both: the export for the number, the API for the
rest. Both are stored, side by side, in the same row.

## How a recording is linked

`ls_backfill.py` takes each manifest recording and finds it in the API:

1. activity by title, case by title, learner by name: each must match
   exactly one row, ignoring case and spacing, or nothing is written;
2. the activity's events on the recording's local date;
3. that learner's recordings for each event and case, keeping those that
   start within two minutes of our start time (the export truncates it to
   the minute);
4. if more than one is left, the one whose files include all of ours (one
   learner can have two recordings starting in the same second, each with
   its own files);
5. the recording's files, matched to our `videos` rows **by filename**;
6. the learner's responses for the case: only the faculty-observation part
   is kept, as points per section and submission in `case_scores`. Its
   submitter is the preceptor.

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
python scripts/ls_backfill.py --case 261505   # just one
```

`LS_API_KEY` goes in `.env` (gitignored). The key is generated in
LearningSpace under System > Security, with Data API access switched on.

Requests are spaced a second apart (`--pace`) and every reply is cached in
`data/ls_api/cache/`, so a dry run followed by `--apply` asks the server
once; `--refresh` ignores the cache. A run over 57 recordings is about 200
requests. Cached replies contain real names, which is why they live under
`data/`.

`--prune` drops recordings that have no video and nothing else pointing at
them; re-importing the export brings them back.

## What we take, and what we leave

Taken: identifiers, exact start time (UTC), room, file facts and download
links, the observer's name and checklist points. Left in LearningSpace: item
answers, the learner's own quizzes and self-evaluation, and free-text
comments. The SP's name is not in the API for a single recording; it still
comes from the export.

## Known gaps (2026-11-11)

- The 4 remediation recordings from April 2025 are partly linked (above).
- 8 of 114 video files have no file link: they belong to those 4.
- 1 recording (261573) has no observation checklist, so no preceptor or score.
- Download links are stored but not yet used: ingest still copies files by hand.
