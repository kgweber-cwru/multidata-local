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

`asr` writes `data/transcripts/<case_id>/<camera>_<engine>_<model>.json` (plus
a `.txt` sibling for a quick read) — always word-level speaker-labeled
regardless of engine, reusing `data/diarization/<case_id>/<camera>.rttm` if
it's already there rather than re-running pyannote. Default engine is
`faster_whisper` (`--model medium --language en`); `whisperx`/`suite` remain
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
import traceback
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
    audio.extract(row["filepath"], _out("audio", row, ".wav"), loudnorm=args.loudnorm)
    return {"audio_path": str(_out("audio", row, ".wav"))}


def stage_asr(row, args):
    from multidata import asr

    skip = _skip_if_not_audio_camera(row, args, "asr")
    if skip is not None:
        return skip

    wav = row["audio_path"] or _out("audio", row, ".wav")
    out = _out("transcripts", row, f"_{args.engine}_{args.model}.json")

    # `suite` has no model-size selection (fixed remote server); the other
    # two share `model_name`/`language`. Device kwargs only make sense for
    # whisperx -- ctranslate2 keeps the Whisper model itself on `--device`
    # (cpu/cuda only); `--accel-device` governs the torch-based
    # alignment/diarization steps and defaults to the fastest available.
    engine_kwargs = {"language": args.language}
    if args.engine != "suite":
        engine_kwargs["model_name"] = args.model
    if args.engine == "whisperx":
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

    wav = row["audio_path"] or _out("audio", row, ".wav")
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
    log.info("building ELAN file: %s/%s", row["case_id"], row["camera"])
    elan.build_eaf(transcript, row["filepath"], _out("elan", row, ".eaf"))
    return {}


def stage_pose(row, args):
    from multidata import pose

    log.info("extracting pose: %s/%s", row["case_id"], row["camera"])
    pose.extract(row["filepath"], _out("pose", row, ".pkl"),
                device=args.accel_device, profile=args.profile,
                detect_every=args.detect_every)
    return {}


STAGES = {
    "audio": stage_audio,
    "asr": stage_asr,
    "diarize": stage_diarize,
    "elan": stage_elan,
    "pose": stage_pose,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=sorted(STAGES))
    ap.add_argument("--only", help="restrict to one case_id")
    ap.add_argument("--manifest", default=str(ROOT / "manifest.sqlite"))
    ap.add_argument("--engine", default=asr_default(), help="asr/elan: which ASR engine")
    ap.add_argument("--model", default=asr_model_default(),
                    help="asr/elan: model size (e.g. tiny/small/medium/large-v3); "
                         "ignored for --engine suite")
    ap.add_argument("--language", default=asr_language_default(),
                    help="asr: language code, skips auto-detect")
    ap.add_argument("--loudnorm", action="store_true", help="audio: loudness-normalize")
    ap.add_argument("--max-speakers", type=int, help="diarize: upper bound hint")
    ap.add_argument("--device", default="cpu",
                    help="asr (whisperx): Whisper model device -- cpu/cuda only, "
                         "never mps (ctranslate2 doesn't support it)")
    ap.add_argument("--accel-device", default=None,
                    help="asr/diarize/pose: device for the torch/onnxruntime-based "
                         "diarization (and, if set, alignment) and pose-network steps "
                         "-- default auto-detects the fastest available (mps > cuda > cpu)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also print full tracebacks to the console on failure "
                         "(the log file always gets them regardless)")
    ap.add_argument("--profile", action="store_true",
                    help="pose: log a decode/detect/pose timing breakdown per video, "
                         "for diagnosing where time (and GPU/CPU usage) actually goes")
    ap.add_argument("--detect-every", type=int, default=1,
                    help="pose: re-run the (CPU-bound) YOLOX detector only every Nth "
                         "frame, holding bboxes for frames in between -- confirmed via "
                         "--profile that detection is ~88%% of pose runtime; default 1 "
                         "(every frame) is unchanged/safe, try higher after eyeballing "
                         "a render_overlay() render at your chosen stride")
    args = ap.parse_args()

    _setup_logging(args.verbose)

    rows = manifest.pending(args.stage, args.manifest)
    if args.only:
        rows = [r for r in rows if r["case_id"] == args.only]
    # Shortest videos first: on a long unattended batch, this clears the most
    # rows early and surfaces a representative failure sooner than working in
    # arbitrary (created-order) order would.
    rows.sort(key=lambda r: r["duration_s"] or 0)
    log.info("%s: %d pending row(s)", args.stage, len(rows))

    ok = failed = 0
    for row in rows:
        label = f"{row['case_id']}/{row['camera']}"
        log.info("--- %s", label)
        try:
            extra = STAGES[args.stage](row, args) or {}
            status = extra.pop(f"{args.stage}_status", "done")
            manifest.update(row["case_id"], row["camera"], args.manifest,
                            **{f"{args.stage}_status": status}, **extra)
            ok += 1
        except Exception as exc:
            manifest.update(row["case_id"], row["camera"], args.manifest,
                            **{f"{args.stage}_status": "failed"})
            log.error("FAILED %s %s: %s: %s", args.stage, label, type(exc).__name__, exc)
            log.debug("full traceback for %s %s:\n%s", args.stage, label, traceback.format_exc())
            failed += 1

    log.info("%s: %d done, %d failed", args.stage, ok, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
