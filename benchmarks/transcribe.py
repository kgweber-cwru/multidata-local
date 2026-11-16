#!/usr/bin/env python
"""Run named settings over the gold recordings (step 1 of 2; then
`run_benchmark.py` scores what this saved).

    python benchmarks/transcribe.py                          # every setting, every gold recording
    python benchmarks/transcribe.py --settings assemblyai google
    python benchmarks/transcribe.py --recordings 261505 --settings elevenlabs_medical

Settings are named in `benchmarks/configs/settings.yaml`. Each run goes through
`multidata.transcribers` and is saved like any other transcript
(`data/transcripts/<recording>/`, registered in the manifest's `transcripts`
table); a setting that changes more than the model goes under `variants/<name>/`
there (`out_dir`). A transcript that already exists is skipped, so re-running
costs nothing. Each engine response is saved under `responses/<name>/` before
it is normalized, so a failure after the vendor has answered never pays twice.
A setting with `responses_from: <other setting>` reads that setting's saved
responses instead, so it changes only what happens after the vendor answers
(such as `speakers: pipeline`) and calls no vendor for audio already sent.

Cloud engines still refuse a recording without `cloud_release`.
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest, transcribers  # noqa: E402
from multidata.paths import DATA_DIR  # noqa: E402

SETTINGS = ROOT / "benchmarks" / "configs" / "settings.yaml"
REFERENCES = ROOT / "benchmarks" / "references"

# Parameters that can't change the words: the model is already in the file
# name, and the rest are account, transport or clean-up choices.
OPERATIONAL = {"model", "model_name", "enable_logging", "base_url", "timeout_s",
               "poll_interval_s", "delete_after", "delete_staged", "retry_waits_s", "device",
               "compute_type", "align_device", "diarize_device", "batch_size"}


def load_settings(path=SETTINGS):
    return yaml.safe_load(Path(path).read_text())


def engine_params(setting):
    """The engine's parameters: the setting minus `engine` and `responses_from`."""
    return {k: v for k, v in setting.items() if k not in ("engine", "responses_from")}


def is_variant(setting):
    """Does `setting` change anything beyond the model and operational choices?"""
    engine, params = setting["engine"], engine_params(setting)
    defaults = transcribers.params(engine)
    return any(k not in OPERATIONAL and defaults.get(k) != v for k, v in params.items())


def out_dir(recording_id, name, setting):
    base = DATA_DIR / "transcripts" / recording_id
    return base / "variants" / name if is_variant(setting) else base


def gold_recordings():
    return sorted(p.name.removesuffix(".gold.txt") for p in REFERENCES.glob("*.gold.txt"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", nargs="+", help="names from settings.yaml (default: all)")
    ap.add_argument("--recordings", nargs="+", help="default: every gold recording")
    args = ap.parse_args()

    settings = load_settings()
    names = args.settings or list(settings)
    unknown = [n for n in names if n not in settings]
    if unknown:
        sys.exit(f"not in {SETTINGS.name}: {', '.join(unknown)}")

    failed = False
    for recording_id in args.recordings or gold_recordings():
        camera = manifest.ensure_audio_camera(recording_id)
        for name in names:
            setting = settings[name]
            transcriber = transcribers.get_transcriber(setting["engine"], **engine_params(setting))
            target = out_dir(recording_id, name, setting) / f"{camera}_{setting['engine']}_{transcriber.model}.json"
            if target.exists():
                print(f"{recording_id} {name}: exists")
                continue
            try:
                responses = setting.get("responses_from", name)
                result = transcriber.transcribe(
                    recording_id, responses_dir=DATA_DIR / "transcripts" / recording_id / "responses" / responses)
                result.save(out_dir=target.parent)
                print(f"{recording_id} {name}: saved ({result.record['provenance']['wall_s']} s)")
            except Exception as e:
                failed = True
                print(f"{recording_id} {name}: FAILED {type(e).__name__}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
