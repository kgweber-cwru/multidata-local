#!/usr/bin/env python
"""Smoke-test the cloud transcribers on one short excerpt with gold behind it
(docs/status.md, "Cloud ASR"; spec docs/asr_provider_spec.md).

    python scripts/smoke_cloud.py                         # every cloud engine
    python scripts/smoke_cloud.py --engines assemblyai

The default excerpt is 252848, 344.5-404.5 s: chosen from gold as name-free,
two speakers with overlaps, fillers, self-repairs and durations. The excerpt
and everything returned go to data/smoke/<recording>_<start>-<end>/, which
git ignores.

Each vendor's raw response is saved the moment it arrives and reused on any
later run, so fixing a normalizer and running again costs nothing; pass
--fresh to call the vendor again. Nothing is registered in the manifest's
`transcripts` table: these are excerpts, not runs to benchmark.

Prints, per engine: L0/L1 WER against the gold for the same span (names
redacted on both sides, as `run_benchmark.py` scores), word and filler counts
against gold, and how many speakers the engine heard (gold has two).
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from multidata import manifest, transcribers  # noqa: E402
from multidata.elan import GOLD_TEXT_TIERS, _tier_annotations, words_of  # noqa: E402
from multidata.paths import DATA_DIR  # noqa: E402
from run_benchmark import hypothesis_text, score  # noqa: E402

FILLER = re.compile(r"\b(?:um+|uh+|mm-hmm|mm+|uh-huh|hmm+)\b", re.I)


def cut_excerpt(recording_id, start, end, out):
    """The excerpt of the recording's canonical audio, cut once."""
    if out.exists():
        return out
    camera = manifest.ensure_audio_camera(recording_id)
    source = transcribers.base._audio_path(recording_id, camera, manifest.DEFAULT_PATH)
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(end - start),
                    "-i", str(source), "-c:a", "pcm_s16le", str(out)], check=True)
    return out


def gold_text(recording_id, start, end):
    """Gold utterances starting inside [start, end), in time order, unredacted
    (score() redacts)."""
    import pympi

    eaf = pympi.Elan.Eaf(str(DATA_DIR / "gold" / recording_id / f"{recording_id}.gold.eaf"))
    spans = sorted(s for tier in GOLD_TEXT_TIERS for s in _tier_annotations(eaf, tier))
    return " ".join(text for s, _, text in spans if start <= s < end)


def cache_calls(transcriber, raw_path, fresh):
    """Save the vendor's response before anything else touches it, and reuse it."""
    call = transcriber.call

    def cached(audio, recording_id, camera):
        if raw_path.exists() and not fresh:
            saved = json.loads(raw_path.read_text())
            return saved["native"], saved["meta"]
        native, meta = call(audio, recording_id, camera)
        raw_path.write_text(json.dumps({"native": native, "meta": meta}, indent=2))
        return native, meta

    transcriber.call = cached


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recording", default="252848")
    ap.add_argument("--start", type=float, default=344.5)
    ap.add_argument("--end", type=float, default=404.5)
    ap.add_argument("--engines", nargs="+", default=sorted(transcribers.CLOUD))
    ap.add_argument("--fresh", action="store_true", help="call vendors again even if a response is saved")
    ap.add_argument("--elevenlabs-logging", action="store_true",
                    help="enable_logging=True: ElevenLabs keeps the audio. Off by default; "
                         "zero-retention is refused below the enterprise tier")
    args = ap.parse_args()
    engine_params = {"elevenlabs": {"enable_logging": args.elevenlabs_logging}}

    out_dir = DATA_DIR / "smoke" / f"{args.recording}_{args.start:g}-{args.end:g}"
    out_dir.mkdir(parents=True, exist_ok=True)
    clip = cut_excerpt(args.recording, args.start, args.end, out_dir / "excerpt.wav")
    names = manifest.names_for_recording(args.recording)
    ref = gold_text(args.recording, args.start, args.end)
    (out_dir / "gold.txt").write_text(ref + "\n")
    print(f"gold: {len(ref.split())} words, {len(FILLER.findall(ref))} fillers, 2 speakers\n")

    failed = False
    for engine in args.engines:
        transcriber = transcribers.get_transcriber(engine, speakers="engine",
                                                   **engine_params.get(engine, {}))
        cache_calls(transcriber, out_dir / f"{engine}.raw.json", args.fresh)
        try:
            t = transcriber.transcribe(args.recording, audio_path=clip)
        except Exception as e:
            print(f"{engine}: FAILED {type(e).__name__}: {e}\n")
            failed = True
            continue
        (out_dir / f"{engine}.json").write_text(json.dumps(t.record, indent=2))
        hyp = hypothesis_text(t.record)
        (out_dir / f"{engine}.txt").write_text(hyp + "\n")
        speakers = {w.get("speaker") for w in words_of(t.record)} - {None}
        prov = t.record["provenance"]
        print(f"{engine} ({t.model}; returned {prov.get('model_returned')}; {prov['wall_s']} s)")
        print(f"  {score(ref, hyp, names)}")
        print(f"  {len(hyp.split())} words, {len(FILLER.findall(hyp))} fillers, "
              f"{len(speakers)} speakers, {len(t.record['segments'])} segments")
        if prov.get("notes"):
            print(f"  notes: {prov['notes']}")
        print()
    print(f"files: {out_dir.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
