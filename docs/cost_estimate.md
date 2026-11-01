# Cost estimate for ingesting and processing cases

As of 2026-11-01. Measured on `tofino` (RTX 5080, 48 CPU cores, 61 GB RAM) from
the manifest's `stage_runs` table: 110 videos, 57 cases, 73 h of video, all in
the small exam rooms. Blank lines (`________`) mark costs we can't measure yet,
mostly the 3D stages on the [roadmap](https://claude.ai/artifact/QTY1PoyKCDDb8qeX1Lxn1m).

**Bottom line:** compute is about **0.65 machine-hours per case**, and 2D pose
is 90% of it. Every 100 cases is about 2.7 days of tofino running nonstop.
Storage is about **0.56 GB per case**, almost all raw video. The full
small-room catalogue (1,281 cases) would take about a month of compute and
**more disk than tofino's main drive has free**.

## What an average case is

| | Per case |
|---|---|
| Video files | 1.93 (most cases have both cameras; 4 of 57 have one) |
| Hours of video (all cameras) | 1.28 h |
| Hours of audio transcribed (one camera per case) | 0.66 h |

Recordings run 20 to 70 minutes, so a case can cost half or double these
averages.

## Compute per stage

Speed is media time processed per hour of machine time ("realtime" = 1x).

| Stage | Runs on | Measured speed | Per case | Per 100 cases | Notes |
|---|---|---|---|---|---|
| audio | CPU | ~2,000x | 2 s | 3 min | ffmpeg extract |
| asr (+ diarization) | GPU | 13.5x | 2.9 min | 4.9 h | faster-whisper medium + pyannote, one camera per case. Measured while pose was running, so likely pessimistic |
| elan | CPU | instant | <1 s | <1 min | |
| sync | CPU | ~1,900x | 3 s | 5 min | audio offsets + frame times |
| views | CPU, 1 worker | 81x | 57 s | 1.6 h | must run as one worker |
| **pose (2D)** | **GPU, 2 workers** | **2.19x combined** | **35 min** | **58 h** | 1.1x per worker; one worker alone ran 2.2x, so two workers give ~25% more. Detection is 80% of pose time |
| **Total** | | | **~39 min** | **~65 h (2.7 days)** | |

The GPU is the bottleneck: pose and ASR can't usefully run at the same time.

**Ways to cut pose time, not yet tested:**

- Run the person detector every 2nd or 3rd frame (`--detect-every`). Detection is 80% of the time, so this could nearly halve it; check overlays first. Measured speedup: ________
- Skip frames with nobody in them more cheaply. About a third of frames are empty. Measured speedup: ________

## Storage per case

| Data | Per hour of media | Per case | Per 100 cases |
|---|---|---|---|
| Raw video | 364 MB | 466 MB | 47 GB |
| Audio (16 kHz WAV) | 115 MB | 75 MB | 7.5 GB |
| Pose (HALPE_26 `.npz`) | 13 MB | 17 MB | 1.7 GB |
| Views (stretches + library) | 3 MB | 4 MB | 0.4 GB |
| Transcripts, diarization, ELAN, sync | | 2 MB | 0.2 GB |
| **Total** | | **~0.56 GB** | **~56 GB** |

Free space today: 681 GB on tofino's main drive, 14 TB on the `/wddata`
backup array. The nightly backup mirrors everything, so each case also costs
the same again on `/wddata`.

## Scenarios

| Scenario | Cases | Compute (tofino nonstop) | Storage (+ same again in backup) |
|---|---|---|---|
| Now | 57 | done | 32 GB |
| Fill thin rooms: 3 more cases in each of 15 rooms | +45 | ~1.2 days | +25 GB |
| +100 cases | +100 | ~2.7 days | +56 GB |
| Every small-room case in the catalogue | 1,281 | ~35 days | ~720 GB: **more than the 681 GB free**. Needs raw video moved to `/wddata` or new disk |

## People's time (fill in)

| Task | Time per unit | Unit | Notes |
|---|---|---|---|
| Find and download a case's videos | ________ | case | Kate is timing this |
| Register a case (ingest steps 3-4) | ________ | case | could be scripted |
| Check overlays and failures after a batch | ________ | batch | |
| Name camera views (presets) | ________ | room | 62 provisional labels to confirm |
| Gold transcript (two passes) | ________ | case | see `annotation/README.md` |
| Calibration session | ~3 h room time, 2-3 people | room | room 06 first |
| Short follow-up visit (measure + preset shots) | ________ | room | 14 more small rooms |

## 3D stages (not built yet; fill in as the roadmap reaches them)

| Stage | Compute per case | People's time | Notes |
|---|---|---|---|
| Lens calibration per zoom level | ________ | ________ | once per camera, per zoom |
| Camera position per preset | ________ | ________ | once per room, per preset |
| Locating off-preset camera views | ________ | ________ | 44% of footage is off-preset or unrecognised |
| Triangulation + cross-view matching | ________ | | |
| Mirror filter | ________ | | |
| Identity (learner / preceptor / patient) | ________ | ________ | |
| 3D review / QA | | ________ | |

## Refreshing these numbers

Every stage run adds a row to `stage_runs`. To recompute the speed table:

```sql
SELECT stage, COUNT(*) AS runs,
       ROUND(SUM(media_s) / 3600, 1) AS media_h,
       ROUND(SUM(wall_s) / 3600, 2) AS machine_h,
       ROUND(SUM(media_s) / SUM(wall_s), 1) AS speed_x
FROM stage_runs WHERE status = 'done' GROUP BY stage;
```

Pose runs from several workers overlap. For combined pose throughput, divide
media hours by the hours when at least one worker was running, not by the sum
of `wall_s`.
