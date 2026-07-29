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
"""
import argparse
import datetime
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest  # noqa: E402

DATA = ROOT / "data"
LOG = ROOT / "logs" / "run_stage.log"


def _out(kind, row, suffix):
    """data/<kind>/<case_id>/<camera><suffix>, parents created."""
    p = DATA / kind / row["case_id"] / f"{row['camera']}{suffix}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def asr_default():
    """asr.py's default engine, imported lazily to keep the env split intact."""
    from multidata import asr

    return asr.DEFAULT_ENGINE


def stage_audio(row, args):
    from multidata import audio

    audio.extract(row["filepath"], _out("audio", row, ".wav"), loudnorm=args.loudnorm)
    return {"audio_path": str(_out("audio", row, ".wav"))}


def stage_asr(row, args):
    from multidata import asr

    wav = row["audio_path"] or _out("audio", row, ".wav")
    asr.transcribe(wav, _out("transcripts", row, f"_{args.engine}.json"),
                   engine=args.engine)
    return {"asr_model": args.engine}


def stage_diarize(row, args):
    from multidata import diarize

    wav = row["audio_path"] or _out("audio", row, ".wav")
    diarize.diarize(wav, _out("diarization", row, ".rttm"),
                    max_speakers=args.max_speakers)
    return {}


def stage_elan(row, args):
    from multidata import elan

    transcript = _out("transcripts", row, f"_{args.engine}.json")
    if not transcript.exists():
        raise FileNotFoundError(f"{transcript} — run the asr stage first")
    elan.build_eaf(transcript, row["filepath"], _out("elan", row, ".eaf"))
    return {}


def stage_pose(row, args):
    from multidata import pose

    pose.extract(row["filepath"], _out("pose", row, ".pkl"))
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
    ap.add_argument("--loudnorm", action="store_true", help="audio: loudness-normalize")
    ap.add_argument("--max-speakers", type=int, help="diarize: upper bound hint")
    args = ap.parse_args()

    rows = manifest.pending(args.stage, args.manifest)
    if args.only:
        rows = [r for r in rows if r["case_id"] == args.only]
    print(f"{args.stage}: {len(rows)} pending row(s)")

    ok = failed = 0
    for row in rows:
        label = f"{row['case_id']}/{row['camera']}"
        print(f"--- {label}")
        try:
            extra = STAGES[args.stage](row, args) or {}
            manifest.update(row["case_id"], row["camera"], args.manifest,
                            **{f"{args.stage}_status": "done"}, **extra)
            ok += 1
        except Exception:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a") as f:
                f.write(f"\n=== {datetime.datetime.now().isoformat()} "
                        f"{args.stage} {label}\n{traceback.format_exc()}")
            manifest.update(row["case_id"], row["camera"], args.manifest,
                            **{f"{args.stage}_status": "failed"})
            failed += 1
            print(f"    FAILED — see {LOG}")

    print(f"{args.stage}: {ok} done, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
