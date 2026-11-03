# Encounter Explorer (proof-of-concept demo)

A local web app that shows what this dataset can do, for researchers and
leadership. Two views:

- **One encounter.** Both cameras, time-synced, with sound, optional
  pixelated faces and 2D skeletons coloured by role. Below them, lanes that
  move with the playhead: who talks when (with pauses), speech rate, pitch
  per speaker, loudness, each person's body movement, where each person's
  head points (a gaze proxy) and the camera's preset view. A transcript
  beside it scrolls along; click any line, lane or bookmarked moment to jump
  there. Where a case has one, a **3D view** under the small camera shows the
  people reconstructed from both cameras, orbitable, during a stretch when
  neither camera moves (purple bar on the timeline).
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
python demo/build_case.py 261320                   # the neuro exam (machine transcript)
python demo/fake3d.py 261320 --from 451 --to 688   # seconds: 3D for a still-camera stretch
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
- **Head direction** is not eye tracking. It reads the face direction
  (ears to nose) and how far the head tips down, on a camera that sees
  faces from the side (`"head_direction": true` in the labels file; not on
  overhead cameras). Thresholds were set by eye on room 11's camera 16:
  "toward the other person", "down" (in 261456, the clipboard) or
  "elsewhere". Where a case has 3D and its people are labelled (261320,
  7:31-11:28), head direction comes from the 3D reconstruction instead,
  which works from any camera angle: "toward" is within 35 degrees of the
  other person's head, "down" is tipped more than 35 degrees below level.
  In an exam, looking at the patient's hands or legs counts as down or
  elsewhere, not toward. Camera 13 in that room sees the patient from
  above and the learner from behind, which the 2D method misreads (it had
  the patient "looking down" 97% of the time), so it isn't used there.
- **Gaps in the lanes are real.** In 261456, camera 16 recorded nothing for
  232 s of the 20 minutes (129 dropouts of 0.7-7.5 s; the file's own frame
  timestamps show them), so the video freezes and the pose lanes are blank
  there. The sync stage logs dropouts per camera; most cameras have none.
- **The transcript is re-timed to the video on such cameras.** Camera 16
  also dropped sound in those gaps without leaving silence, so the
  pipeline's wav (which the transcripts, ELAN kits and prosody are timed
  on) is up to 15 s shorter than the video and runs ahead of it.
  `build_case.py` matches the two soundtracks every 10 s and shifts
  transcript, pitch and loudness onto the video's clock (`transcript_clock`
  in `meta.json`). Of 72 camera recordings, three are affected, all room
  11's camera 16: 261456 (14.8 s), 261505 (9.6 s) and 261485 (3.6 s).
  Bookmark times are video time.
- **3D is uncalibrated** (`fake3d.py`). With no calibration session yet,
  the cameras calibrate themselves: focal length from the room's vanishing
  points (floor tiles, door frames), camera positions from people seen by
  both, scale from typical body proportions, up from vertical room lines.
  Shape should be right and size roughly right (about +-10%). All four
  cameras in the two rooms tried came out 2.6-2.9 m up and points reproject
  within about 1 px, which is plausible but not verified. When the case's
  labels file names people on the first camera, the 3D people carry those
  names (261320); otherwise "patient?" and "learner?" are a guess (the
  steadiest person is the patient).

## Files

| File | What it does |
|---|---|
| `build_case.py` | one case -> `data/demo/cases/<case>/` (video, skeletons, lanes) |
| `build_overview.py` | all cases -> `data/demo/overview.json` |
| `tracks.py` | joins per-frame pose detections into tracklets |
| `fake3d.py` | uncalibrated 3D for a still-camera stretch -> `threed.json` |
| `vanishing.py` | focal length and directions from a frame's vanishing points |
| `tracklet_sheets.py` | contact sheets of tracklets, for labelling people |
| `labels/<case>.json` | which tracklet is which person (hand-checked); for a machine transcript, which diarized voice is who (`speakers`) |
| `bookmarks/<case>.json` | moments to jump to |
| `serve.py` | the server; supports the range requests video seeking needs |
| `Dockerfile` | container image: `app` (bundles mounted at /data) or `with-data` |
| `deploy_gcp.sh` | upload bundles, push the image, deploy to Cloud Run |
| `app/` | the page: `index.html`, `app.js`, `style.css` (no libraries) |

Everything under `data/demo/` holds identifiable video and speech: it is
never committed. `serve.py` answers only this machine unless you pass
`--host`; anyone who can reach it can see everything it serves.

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

## Deploying to Google Cloud

The container (`Dockerfile`) is the server and page only; the built bundles
(`data/demo/cases/`, `data/demo/overview.json`) live in a Cloud Storage
bucket that Cloud Run mounts read-only at `/data`. Adding an encounter is
then: build it locally, `deploy_gcp.sh data`, done; no new image.

```bash
export GCP_PROJECT=cwru-sim-center-data GCP_REGION=us-central1 GCP_REPO=encounter-explorer GCP_BUCKET=demo-site-storage-bucket
demo/deploy_gcp.sh all          # or: data, image, service, one at a time
demo/deploy_gcp.sh grant a@umich.edu b@umich.edu c@umich.edu d@umich.edu
demo/deploy_gcp.sh viewers      # who's in; `revoke <email>` takes someone out
```

Needs `docker`, `gsutil` and `gcloud` (the Google Cloud CLI), logged in to
the project. The script creates the bucket and the Artifact Registry
repository if they're missing. `service` deploys the newest pushed image
(or `IMAGE=...`), without public access, and turns on **Identity-Aware
Proxy**: viewers open the service URL, sign in with their Google account,
and get in only if `grant` added them. (A plain `roles/run.invoker` grant
doesn't work for people using a browser: Cloud Run would then want an
identity token on every request, which browsers don't send.)

One-time setup the script doesn't do: if the project has never used IAP,
configure the OAuth consent screen (internal, if all viewers are in the
university's Google Workspace); and give the service's runtime service
account `roles/storage.objectViewer` on the bucket. IAP for Cloud Run is
recent: if gcloud refuses a flag, see
https://cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run.

How it fits Cloud Run:

- **Port and settings** come from the environment: `PORT` (Cloud Run sets
  it), `HOST=0.0.0.0` and `DEMO_DATA=/data` (set in the image).
- **32 MiB response limit.** Cloud Run caps HTTP/1 responses at 32 MiB, and
  browsers ask for video as "everything from byte N". `serve.py` answers
  those in 8 MiB pieces and the browser asks for the next one, so videos
  of any length play and seek.
- **Bucket mount** needs the second-generation execution environment
  (`--execution-environment gen2`, set by the script).
- **Size.** About 0.6 GB per encounter in the bucket; the image is about
  190 MB. Media is sent with `Cache-Control: private, max-age=86400`, so a
  viewer's browser doesn't fetch the same video twice in a day.

Without a bucket (any Docker host, or to try it locally):

```bash
docker build -f demo/Dockerfile --target app -t encounter-explorer:app .
docker run --rm -p 8080:8080 -v "$PWD/data/demo:/data:ro" encounter-explorer:app
# or bake the bundles in:
docker build -f demo/Dockerfile --target with-data -t encounter-explorer:data .
docker run --rm -p 8080:8080 encounter-explorer:data
```
