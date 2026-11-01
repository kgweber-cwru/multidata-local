#!/usr/bin/env python
"""Manifest-driven, resumable batch runner (doc §10).

    python scripts/run_stage.py audio
    python scripts/run_stage.py asr --only 132704
    python scripts/run_stage.py pose            # md-pose env

Reads manifest.sqlite, processes only `pending` rows for the stage, writes
outputs, updates status, appends failures to logs/run_stage.log. Boring and
restartable: kill it, rerun it, it picks up where it left off.

Stage modules are imported lazily, one per stage, so this script runs in either
conda env — `pose` needs md-pose, everything else needs md-speech.

`audio`/`asr`/`diarize`/`elan` are per-case, not per-camera: a case's two
camera files share one audio feed (see `manifest.ensure_audio_camera`), so
only the case's canonical camera does the work — the other camera's row for
these stages gets marked `skipped`, not run twice. `pose` stays per-camera;
the two viewpoints are genuinely different data.

`sync` (md-speech) writes each camera's frame timestamps to
`data/sync/<case_id>/<camera>.npz` and its audio offset from the case's
reference camera to `videos.sync_offset_s` -- what triangulation needs to pair
the two cameras' frames by time. See `multidata.sync`.

`asr` writes `data/transcripts/<case_id>/<camera>_<engine>_<model>.json` (plus
a `.txt` sibling for a quick read) — always word-level speaker-labeled
regardless of engine, reusing `data/diarization/<case_id>/<camera>.rttm` if
it's already there rather than re-running pyannote. Default engine is
`faster_whisper` (`--model medium --language en`); `whisperx` remains
available via `--engine` for testing/comparison.

Logging: INFO-level progress (with timestamps) goes to both the console and
`logs/run_stage.log` — one line per row started, plus whatever the stage
module itself logs along the way (e.g. "diarizing 261498/20"). A failed row
gets one concise `FAILED ...: ExceptionType: message` line in both places;
the full traceback is only ever written to the log file (DEBUG), not blurted
to the console. Pass `-v/--verbose` to also print full tracebacks live.

If you background/redirect this process's own stdout+stderr into a file for
an overnight run, don't point that at `logs/run_stage.log` itself — this
script already writes everything meaningful there via `logging`. Send raw
stderr to `/dev/null` instead (e.g. `... >/dev/null 2>&1 &`); some native
libraries (torchcodec/PyAV) print harmless but unfilterable ObjC runtime
warnings straight to the OS stderr fd, and redirecting that into the same
file just interleaves noise into an otherwise clean log.
"""
import argparse
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest  # noqa: E402

DATA = ROOT / "data"
LOG = ROOT / "logs" / "run_stage.log"

log = logging.getLogger("run_stage")


def _setup_logging(verbose=False):
    """Console: INFO by default (DEBUG with -v). File: always DEBUG, so a
    failure's full traceback is on disk even when the console only got the
    concise one-liner."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.FileHandler(LOG)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def _out(kind, row, suffix):
    """data/<kind>/<case_id>/<camera><suffix>, parents created."""
    p = DATA / kind / row["case_id"] / f"{row['camera']}{suffix}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _abs(path_str):
    """Manifest paths are repo-relative (so the same manifest works on every
    machine); resolve against ROOT, not the cwd. Absolute paths pass through."""
    return ROOT / path_str


def _parse_shard(value):
    """"i/n" -> (i, n), validated. For running multiple concurrent workers
    against the same manifest without them racing on the same rows: `pending()`
    has no claim/lock mechanism, so two unsharded workers would both start from
    the same sorted row list and duplicate work rather than parallelize it."""
    try:
        idx_str, total_str = value.split("/")
        idx, total = int(idx_str), int(total_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} must look like 'i/n', e.g. '0/2'")
    if total < 1 or not (0 <= idx < total):
        raise argparse.ArgumentTypeError(f"{value!r}: need 0 <= i < n, n >= 1")
    return idx, total


def asr_default():
    """asr.py's default engine, imported lazily to keep the env split intact."""
    from multidata import asr

    return asr.DEFAULT_ENGINE


def asr_model_default():
    from multidata import asr

    return asr.DEFAULT_MODEL


def asr_language_default():
    from multidata import asr

    return asr.DEFAULT_LANGUAGE


def _skip_if_not_audio_camera(row, args, stage):
    """None if this row is the case's canonical audio camera (proceed);
    otherwise the `{stage}_status: skipped` dict a stage function should
    return immediately.

    audio/asr/diarize/elan are per-*case*, not per-camera — a case's two
    camera files share one audio feed (confirmed empirically; see
    `manifest.ensure_audio_camera`), so only the chosen camera's row does the
    work. `pose` doesn't call this; it's per-camera.
    """
    from multidata import manifest

    chosen = manifest.ensure_audio_camera(row["case_id"], args.manifest)
    if row["camera"] != chosen:
        return {f"{stage}_status": "skipped"}
    return None


def stage_audio(row, args):
    from multidata import audio

    skip = _skip_if_not_audio_camera(row, args, "audio")
    if skip is not None:
        return skip

    log.info("extracting audio: %s/%s", row["case_id"], row["camera"])
    audio.extract(_abs(row["filepath"]), _out("audio", row, ".wav"), loudnorm=args.loudnorm)
    return {"audio_path": str(_out("audio", row, ".wav").relative_to(ROOT))}


def stage_asr(row, args):
    from multidata import asr

    skip = _skip_if_not_audio_camera(row, args, "asr")
    if skip is not None:
        return skip

    wav = _abs(row["audio_path"]) if row["audio_path"] else _out("audio", row, ".wav")
    out = _out("transcripts", row, f"_{args.engine}_{args.model}.json")

    # Every engine takes `model_name`/`language`. Device kwargs only make sense for the
    # whisperx family -- ctranslate2 keeps the Whisper model itself on
    # `--device` (cpu/cuda only; left unset it auto-detects, see the flag's
    # help); `--accel-device` governs the torch-based alignment/diarization
    # steps and defaults to the fastest available.
    engine_kwargs = {"language": args.language, "model_name": args.model}
    if args.engine in asr.TAKES_DIARIZE_DEVICE:
        if args.device is not None:
            engine_kwargs["device"] = args.device
        if args.accel_device is not None:
            engine_kwargs["align_device"] = args.accel_device

    log.info("transcribing (%s/%s): %s/%s",
             args.engine, args.model, row["case_id"], row["camera"])

    # `diarize_rttm` lets non-self-diarizing engines (faster_whisper) reuse an
    # already-computed diarization instead of paying for pyannote twice.
    asr.transcribe(wav, out, engine=args.engine,
                   diarize_rttm=_out("diarization", row, ".rttm"),
                   diarize_device=args.accel_device,
                   max_speakers=args.max_speakers,
                   **engine_kwargs)
    return {"asr_model": f"{args.engine}/{args.model}", "diarize_status": "done"}


def stage_diarize(row, args):
    from multidata import diarize

    skip = _skip_if_not_audio_camera(row, args, "diarize")
    if skip is not None:
        return skip

    wav = _abs(row["audio_path"]) if row["audio_path"] else _out("audio", row, ".wav")
    log.info("diarizing: %s/%s", row["case_id"], row["camera"])
    diarize.diarize(wav, _out("diarization", row, ".rttm"),
                    max_speakers=args.max_speakers, device=args.accel_device)
    return {}


def stage_elan(row, args):
    from multidata import elan

    skip = _skip_if_not_audio_camera(row, args, "elan")
    if skip is not None:
        return skip

    transcript = _out("transcripts", row, f"_{args.engine}_{args.model}.json")
    if not transcript.exists():
        raise FileNotFoundError(f"{transcript} — run the asr stage first")
    wav = _abs(row["audio_path"]) if row["audio_path"] else _out("audio", row, ".wav")
    # Both media links in the .eaf are repo-relative, like the manifest, so
    # they survive the tree being mounted at a different absolute path.
    wav_rel = wav.relative_to(ROOT)
    # Pitch tier from the prosody stage when it has run (audio-only, so it is
    # the same whichever ASR produced the words). Run prosody before elan.
    pitch = None
    pros = _out("prosody", row, ".npz")
    if pros.exists():
        from multidata import prosody
        pitch = prosody.voiced_stretches(prosody.load(pros))
    else:
        log.warning("no prosody for %s/%s yet: ELAN file gets no pitch tier "
                    "(run prosody, then elan again)", row["case_id"], row["camera"])
    log.info("building ELAN file: %s/%s", row["case_id"], row["camera"])
    elan.build_eaf(transcript, row["filepath"], _out("elan", row, ".eaf"), wav_path=wav_rel,
                   pitch=pitch)
    return {"_details": {"pitch_stretches": len(pitch) if pitch else 0}}


def stage_pose(row, args):
    from multidata import pose

    log.info("extracting pose: %s/%s/%s seconds", row["case_id"], row["camera"], row["duration_s"])
    _, details = pose.extract(_abs(row["filepath"]), _out("pose", row, ".npz"),
                              device=args.accel_device, profile=args.profile,
                              detect_every=args.detect_every)
    return {"_details": details}


def stage_sync(row, args):
    """Frame timestamps for this file, and its audio offset from the case's
    reference camera (`cases.audio_camera`, offset 0) -- see multidata.sync.
    Each row is independent: the non-reference row measures itself against
    the reference camera's audio. Audio for a camera without a kept wav is
    extracted to a temp dir and thrown away."""
    from multidata import sync

    case_id, camera = row["case_id"], row["camera"]
    video = _abs(row["filepath"])

    times = sync.frame_times(video)
    sync.save_frame_times(times, _out("sync", row, ".npz"))
    d = sync.dropped(times)
    log.info("frame times: %s/%s  %d frames, %d gaps, %.1f s lost (max gap %.2f s)",
             case_id, camera, len(times), d["gaps"], d["lost_s"], d["max_gap_s"])

    ref = manifest.ensure_audio_camera(case_id, args.manifest)
    if camera == ref:
        # the reference defines the clock; nothing to measure
        return {"sync_offset_s": 0.0, "sync_agreement": None}

    ref_video = manifest.get_video(case_id, ref, args.manifest)
    if ref_video is None:
        raise KeyError(f"reference camera {ref} has no video row for case {case_id}")
    ref_row = {"camera": ref, "audio_path": ref_video.audio_path, "filepath": ref_video.filepath}
    with sync.tempdir() as tmp:
        wavs = []
        for r in (ref_row, row):
            kept = _abs(r["audio_path"]) if r["audio_path"] else DATA / "audio" / case_id / f"{r['camera']}.wav"
            if not kept.exists():
                kept = sync.extract_audio(_abs(r["filepath"]), Path(tmp) / f"{r['camera']}.wav")
            wavs.append(kept)
        offset, m = sync.offset_from_audio(*wavs)

    log.info("audio offset: %s/%s vs %s  %s", case_id, camera, ref, sync.summary(m))
    if offset is None or m["agreement"] < sync.MIN_AGREEMENT:
        log.warning("sync FAILED for %s/%s: audio windows don't agree on one offset "
                    "(agreement %.2f, %d windows) -- not writing an offset",
                    case_id, camera, m["agreement"], m["windows"])
        return {"sync_status": "failed", "sync_offset_s": None,
                "sync_agreement": m["agreement"]}
    return {"sync_offset_s": round(offset, 6), "sync_agreement": m["agreement"]}


def stage_prosody(row, args):
    """Pitch, loudness and voice quality (multidata.prosody): frame features
    plus a per-speaker summary from the diarization. Per case, audio camera
    only; needs the audio and asr (or diarize) stages first."""
    from multidata import prosody

    skip = _skip_if_not_audio_camera(row, args, "prosody")
    if skip is not None:
        return skip
    wav = _abs(row["audio_path"]) if row["audio_path"] else _out("audio", row, ".wav")
    rttm = _out("diarization", row, ".rttm")
    if not rttm.exists():
        raise FileNotFoundError(f"{rttm} -- run asr (or diarize) first")
    log.info("prosody: %s/%s", row["case_id"], row["camera"])
    feats = prosody.frame_features(wav)
    prosody.save(feats, _out("prosody", row, ".npz"))
    rows = prosody.speaker_summary(feats, prosody.read_rttm(rttm))
    prosody.write_speakers_csv(rows, _out("prosody", row, "_speakers.csv"))
    prosody.export_praat(feats, prosody.read_rttm(rttm), _out("prosody", row, ".npz"))
    manifest.set_speaker_prosody(row["case_id"], row["camera"], rows, args.manifest)
    voiced = sum(1 for x in feats["f0_hz"] if x == x) / max(len(feats["f0_hz"]), 1)  # x == x: not NaN
    return {"_details": {"frames": len(feats["time_s"]), "speakers": len(rows),
                         "voiced_frac": round(voiced, 3), **feats["params"]}}


_view_libraries = {}


def stage_views(row, args):
    """Steady stretches and which view each is (multidata.views). Oldest
    videos first, one worker: views are added to a shared per-camera library."""
    from multidata import views

    cam = row["camera"]
    if cam not in _view_libraries:
        _view_libraries[cam] = views.Library(DATA / "views" / "library", cam)
    lib = _view_libraries[cam]
    log.info("finding camera views: %s/%s", row["case_id"], cam)
    result, new = views.process(_abs(row["filepath"]), cam, lib, row["sync_offset_s"])
    views.save(result, _out("views", row, ".json"))
    for vid in new:
        manifest.add_camera_view(cam, vid, row["case_id"],
                                 (lib.dir / f"{vid}.png").relative_to(ROOT), args.manifest)
    manifest.set_video_views(row["case_id"], cam, views.usage(result), args.manifest)
    st = result["stretches"]
    details = {"keyframes": result["keyframes"], "stretches": len(st),
               "moves": len(st) - 1, "new_views": new,
               "seconds_unassigned": round(sum(s["dur_s"] for s in st if not s["view"]), 1)}
    log.info("%s/%s: %d stretches, %d new view(s) %s", row["case_id"], cam, len(st),
             len(new), new)
    return {"_details": details}


STAGES = {
    "audio": stage_audio,
    "asr": stage_asr,
    "diarize": stage_diarize,
    "elan": stage_elan,
    "pose": stage_pose,
    "sync": stage_sync,
    "views": stage_views,
    "prosody": stage_prosody,
}


# Stages scoped to the small-room 3D pilot: large-room rows stay pending
# (untouched) unless --all-rooms. Transcription stages run on every room.
PILOT_SMALL_ROOMS = {"pose", "sync", "views"}


# The module each stage needs, imported once up front: run in the wrong conda
# env, a stage should stop with one clear message, not fail every row.
STAGE_MODULES = {"audio": "audio", "asr": "asr", "diarize": "diarize", "elan": "elan",
                 "pose": "pose", "sync": "sync", "views": "views", "prosody": "prosody"}
STAGE_ENVS = {"pose": "md-pose", "views": "md-pose"}


def _check_env(stage):
    import importlib

    try:
        importlib.import_module(f"multidata.{STAGE_MODULES[stage]}")
    except ImportError as exc:
        sys.exit(f"{stage}: can't import its module here ({exc}). "
                 f"Run it in the {STAGE_ENVS.get(stage, 'md-speech')} env.")


def _run_env():
    """Where and on what code a batch ran -- recorded with every stage run."""
    import platform
    import subprocess

    def out(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                  cwd=ROOT).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    commit = out(["git", "rev-parse", "--short", "HEAD"])
    if commit and out(["git", "status", "--porcelain", "--untracked-files=no"]):
        commit += "-dirty"
    gpu = out(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]).splitlines()
    return {"host": platform.node(), "code_commit": commit, "gpu": gpu[0] if gpu else ""}


# The flags that can change each stage's cost or output, recorded per run.
STAGE_PARAMS = {
    "audio": ("loudnorm",),
    "asr": ("engine", "model", "language", "device", "accel_device"),
    "diarize": ("max_speakers", "accel_device"),
    "elan": ("engine", "model"),
    "pose": ("accel_device", "detect_every"),
    "sync": (),
    "views": (),
    "prosody": (),
}


def _run_params(args):
    keep = STAGE_PARAMS.get(args.stage, ()) + ("shard",)
    return {k: getattr(args, k) for k in keep if getattr(args, k, None) not in (None, False)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=sorted(STAGES))
    ap.add_argument("--only", help="restrict to one case_id")
    ap.add_argument("--manifest", default=str(ROOT / "manifest.sqlite"))
    ap.add_argument("--engine", default=asr_default(), help="asr/elan: which ASR engine")
    ap.add_argument("--model", default=asr_model_default(),
                    help="asr/elan: model size (e.g. tiny/small/medium/large-v3)")
    ap.add_argument("--language", default=asr_language_default(),
                    help="asr: language code, skips auto-detect")
    ap.add_argument("--loudnorm", action="store_true", help="audio: loudness-normalize")
    ap.add_argument("--max-speakers", type=int, help="diarize: upper bound hint")
    ap.add_argument("--device", default=None,
                    help="asr (whisperx family): Whisper model device -- cpu/cuda only, "
                         "never mps (ctranslate2 doesn't support it). Default auto-detects "
                         "(multidata.device.best_ct2_device) -- cpu on this Mac, cuda with "
                         "no flag needed on an NVIDIA box.")
    ap.add_argument("--accel-device", default=None,
                    help="asr/diarize/pose: device for the torch/onnxruntime-based "
                         "diarization (and, if set, alignment) and pose-network steps "
                         "-- default auto-detects the fastest available (mps > cuda > cpu)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also print full tracebacks to the console on failure "
                         "(the log file always gets them regardless)")
    ap.add_argument("--profile", action="store_true",
                    help="pose: also log the decode/detect/pose timing breakdown per video "
                         "(it is always recorded in the manifest's stage_runs table)")
    ap.add_argument("--detect-every", type=int, default=1,
                    help="pose: run the person detector only every Nth frame, reusing its "
                         "boxes in between (detection is ~60%% of pose time on the "
                         "RTX 5080); default 1 = every frame. Eyeball a "
                         "render_overlay() render before using a higher stride")
    ap.add_argument("--all-rooms", action="store_true",
                    help="pose/sync/views: include large rooms ('00 LA' etc.); by default "
                         "the 3D pilot runs on small exam rooms only")
    ap.add_argument("--shard", type=_parse_shard, default=None,
                    help="run only every nth row, for coordinating N concurrent workers "
                         "against the same manifest with no overlap -- e.g. '0/2' and "
                         "'1/2' for two workers. Required for concurrency: pending() has "
                         "no claim/lock, so unsharded workers duplicate each other's work")
    args = ap.parse_args()

    _setup_logging(args.verbose)
    from multidata.tools import put_env_on_path
    put_env_on_path()            # libraries that call ffmpeg by name find this env's
    _check_env(args.stage)

    rows = manifest.pending(args.stage, args.manifest)
    if args.stage in PILOT_SMALL_ROOMS and not args.all_rooms:
        from multidata.casematch import is_large_room

        large = [r for r in rows if is_large_room(r["room_number"])]
        if large:
            log.info("%s: leaving %d large-room row(s) pending (pilot is small rooms; "
                     "--all-rooms to include)", args.stage, len(large))
        rows = [r for r in rows if not is_large_room(r["room_number"])]
    if args.only:
        rows = [r for r in rows if r["case_id"] == args.only]
    # Shortest videos first: on a long unattended batch, this clears the most
    # rows early and surfaces a representative failure sooner than working in
    # arbitrary (created-order) order would.
    rows.sort(key=lambda r: r["duration_s"] or 0)
    if args.stage == "views":
        # oldest first, so view ids follow first appearance; one worker only
        if args.shard:
            ap.error("views must run as one worker (it adds to a shared view library)")
        rows.sort(key=lambda r: (r["video_date"], r["video_time"], r["camera"]))
    if args.shard:
        idx, total = args.shard
        rows = rows[idx::total]  # interleaved, not split in half: keeps every
        # worker's share a short+long mix instead of one racing ahead through
        # all the short rows while another is stuck alone on the long tail.
    log.info("%s: %d pending row(s)", args.stage, len(rows))

    env = _run_env()
    ok = failed = 0
    for row in rows:
        label = f"{row['case_id']}/{row['camera']}"
        log.info("--- %s", label)
        started = datetime.now().isoformat(timespec="seconds")
        t0 = time.perf_counter()
        details, error = {}, ""
        try:
            extra = STAGES[args.stage](row, args) or {}
            details = extra.pop("_details", {})
            status = extra.pop(f"{args.stage}_status", "done")
            manifest.update(row["case_id"], row["camera"], args.manifest,
                            **{f"{args.stage}_status": status}, **extra)
            if status == "failed":   # a stage can fail without raising (e.g. sync)
                failed += 1
            else:
                ok += 1
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            manifest.update(row["case_id"], row["camera"], args.manifest,
                            **{f"{args.stage}_status": "failed"})
            log.error("FAILED %s %s: %s", args.stage, label, error)
            log.debug("full traceback for %s %s:\n%s", args.stage, label, traceback.format_exc())
            failed += 1
        wall = time.perf_counter() - t0
        manifest.record_stage_run(
            args.stage, row["case_id"], row["camera"], status, started, wall,
            media_s=row["duration_s"], params=_run_params(args), details=details,
            error=error, device=args.accel_device or args.device or "",
            path=args.manifest, **env)
        if status == "done" and row["duration_s"]:
            log.info("%s %s: %.0f s wall, %.2fx realtime", args.stage, label, wall,
                     row["duration_s"] / wall)

    log.info("%s: %d done, %d failed", args.stage, ok, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
