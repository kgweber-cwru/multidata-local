# Encounter Explorer (proof-of-concept demo)

A local web app that shows what this dataset can do, for researchers and
leadership. Two views:

- **One encounter.** Both cameras, time-synced, with sound, optional
  pixelated faces and 2D skeletons coloured by role. Below them, lanes that
  move with the playhead: who talks when (with pauses), speech rate, pitch
  per speaker, loudness, each person's body movement, and the camera's
  preset view. A transcript beside it scrolls along; click any line, lane or
  bookmarked moment to jump there.
- **All encounters.** Every case on one time axis as a strip of who talks
  when, with simple measures beside it (share of time with speech,
  overlapping speech, longest silence, turn changes per minute, top voice's
  share of the talk). Sortable; click a strip for its transcript.

It lives on its own branch and reads the pipeline's outputs; nothing in
`src/` depends on it.

## Run it

```bash
conda activate md-pose
python demo/build_case.py 261456 --offset 31=0.0   # ~10 min: video encodes on the GPU
python demo/build_overview.py                      # seconds; any env
python demo/serve.py                               # open http://localhost:8765
```

Space plays and pauses, the arrow keys skip 5 s (with Shift, 30 s), and
clicking the small camera makes it the large one.

`build_case.py --skip-video` rebuilds the data in seconds without
re-encoding. `--machine` builds a case from the pipeline transcript (diarized
voices, not roles) when it has no human transcript.

## What's real and what isn't

Say this when you show it:

- **261456** uses a human transcript, but only one annotator's pass 1, not
  adjudicated. Speaker roles in the "all encounters" view are machine
  diarization, which sometimes merges two people or splits one.
- **Camera 31 of 261456** failed audio sync. Its offset (0.0 s) was found by
  matching body movement between the two cameras. On a case with a known
  offset that method was right to about 0.15 s, so expect lip sync on that
  camera to be roughly right.
- **Movement** is how fast each person's keypoints move, in frame-heights per
  second on the large camera. It isn't comparable between cameras: a close
  camera sees hands, a far one the whole body. People were identified by a
  simple tracker plus hand labels (`labels/<case>.json`). Reflections in
  the observation window are hidden by hand.
- **Pitch** is shown only where one person speaks alone. **Loudness** is
  uncalibrated.
- **Pixelation** follows the pose model's head keypoints, so a face it
  missed in a frame isn't covered.

## Files

| File | What it does |
|---|---|
| `build_case.py` | one case -> `data/demo/cases/<case>/` (video, skeletons, lanes) |
| `build_overview.py` | all cases -> `data/demo/overview.json` |
| `tracks.py` | joins per-frame pose detections into tracklets |
| `tracklet_sheets.py` | contact sheets of tracklets, for labelling people |
| `labels/<case>.json` | which tracklet is which person (hand-checked) |
| `bookmarks/<case>.json` | moments to jump to |
| `serve.py` | local server; supports the range requests video seeking needs |
| `app/` | the page: `index.html`, `app.js`, `style.css` (no libraries) |

Everything under `data/demo/` holds identifiable video and speech: it stays
on the processing host and is never committed. `serve.py` answers only this
machine unless you pass `--host`; only do that on a network where everyone
who can reach it is on the study's IRB.

## Adding a case

1. `python demo/build_case.py <case> --skip-video`
2. Label people: `python demo/tracklet_sheets.py <case>` writes contact
   sheets of the longest tracklets to `data/demo/sheets/`; look at them and
   write `labels/<case>.json` (format as in `labels/261456.json`). Without labels everyone is drawn as "person" and
   movement is one lane.
3. Optionally add `bookmarks/<case>.json` (times in seconds, labels without
   names).
4. Build again without `--skip-video`, then rerun `build_overview.py` so the
   overview links to it.
