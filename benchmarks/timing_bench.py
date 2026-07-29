#!/usr/bin/env python
"""Quick wall-clock timing experiment: model size x acceleration, one short
case (252851, ~7.6 min audio) -- NOT the WER/CER benchmark in
run_benchmark.py. This measures speed only, to inform local-vs-NVIDIA-box
decisions, not accuracy.

    python benchmarks/timing_bench.py

Appends one row per (engine, model, device, accel_device, phase) to
benchmarks/results/timing_bench.csv as each phase finishes, so an interrupted
run still leaves partial results on disk. Prints the same as it goes.

Deliberately platform-portable: the Whisper-model device and the
alignment/diarization "accel" device are both picked at runtime
(`multidata.device.best_torch_device` -- mps > cuda > cpu) rather than
hardcoded, so re-running this unchanged on an NVIDIA box exercises cuda
instead of mps and the results.csv rows stay directly comparable (same
schema, `machine` column distinguishes the runs).
"""
import csv
import datetime
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata.device import best_torch_device  # noqa: E402

AUDIO = ROOT / "data" / "audio" / "252851" / "24.wav"
RESULTS = ROOT / "benchmarks" / "results" / "timing_bench.csv"

# ctranslate2 (whisperx's and faster-whisper's shared ASR backend) only
# supports cpu/cuda for the Whisper model itself, never mps.
WHISPER_DEVICE = "cuda" if best_torch_device() == "cuda" else "cpu"
# The wav2vec2 aligner and pyannote diarizer are plain torch models and take
# whatever's fastest here (mps > cuda > cpu).
ACCEL_DEVICE = best_torch_device()

FIELDS = ["timestamp", "case_id", "audio_s", "engine", "model", "device",
          "accel_device", "phase", "seconds", "realtime_factor", "machine"]

MACHINE = platform.platform()


def audio_seconds():
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(AUDIO)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def record(engine, model, device, accel_device, phase, seconds, audio_s):
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    is_new = not RESULTS.exists()
    row = {
        "timestamp": datetime.datetime.now().isoformat(),
        "case_id": "252851",
        "audio_s": round(audio_s, 1),
        "engine": engine,
        "model": model,
        "device": device,
        "accel_device": accel_device or "",
        "phase": phase,
        "seconds": round(seconds, 2),
        "realtime_factor": round(seconds / audio_s, 3),
        "machine": MACHINE,
    }
    with open(RESULTS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            w.writeheader()
        w.writerow(row)
    print(f"    {phase}: {seconds:.1f}s  ({row['realtime_factor']}x realtime)")
    return row


def bench_faster_whisper(model_name, audio_s):
    from faster_whisper import WhisperModel

    print(f"--- faster_whisper / {model_name} / {WHISPER_DEVICE} ---")

    t0 = time.perf_counter()
    model = WhisperModel(model_name, device=WHISPER_DEVICE, compute_type="int8")
    record("faster_whisper", model_name, WHISPER_DEVICE, None, "load",
           time.perf_counter() - t0, audio_s)

    t0 = time.perf_counter()
    segments, info = model.transcribe(
        str(AUDIO), vad_filter=True, condition_on_previous_text=False,
        no_speech_threshold=0.6, compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
    )
    segments = list(segments)  # transcribe() is a lazy generator -- force full decode to time it for real
    record("faster_whisper", model_name, WHISPER_DEVICE, None, "transcribe",
           time.perf_counter() - t0, audio_s)
    print(f"    ({len(segments)} segments)")


def bench_whisperx(model_name, accel_device, audio_s):
    import whisperx

    from multidata.hf_auth import hf_token

    print(f"--- whisperx / {model_name} / device={WHISPER_DEVICE} / accel_device={accel_device} ---")

    audio = whisperx.load_audio(str(AUDIO))

    t0 = time.perf_counter()
    model = whisperx.load_model(model_name, WHISPER_DEVICE, compute_type="int8")
    record("whisperx", model_name, WHISPER_DEVICE, accel_device, "load_model",
           time.perf_counter() - t0, audio_s)

    t0 = time.perf_counter()
    result = model.transcribe(audio, batch_size=8)
    record("whisperx", model_name, WHISPER_DEVICE, accel_device, "transcribe",
           time.perf_counter() - t0, audio_s)

    t0 = time.perf_counter()
    align_model, meta = whisperx.load_align_model(result["language"], accel_device)
    result = whisperx.align(result["segments"], align_model, meta, audio, accel_device)
    record("whisperx", model_name, WHISPER_DEVICE, accel_device, "align",
           time.perf_counter() - t0, audio_s)

    t0 = time.perf_counter()
    dia = whisperx.diarize.DiarizationPipeline(token=hf_token(), device=accel_device)
    diarize_segments = dia(str(AUDIO))
    whisperx.assign_word_speakers(diarize_segments, result)
    record("whisperx", model_name, WHISPER_DEVICE, accel_device, "diarize",
           time.perf_counter() - t0, audio_s)


def bench_diarize_standalone(accel_device, audio_s):
    """The standalone `diarize.diarize()` stage -- what you pay separately if
    your ASR engine has no diarization of its own (faster_whisper, or
    whisperx used purely for transcription). Uses `multidata.diarize`
    directly rather than reimplementing the pyannote call, so this also
    exercises the real stage code (including its `DiarizeOutput` handling).
    """
    from multidata import diarize

    print(f"--- diarize (standalone) / accel_device={accel_device} ---")

    t0 = time.perf_counter()
    diarize.diarize(str(AUDIO), num_speakers=None, max_speakers=4, device=accel_device)
    record("diarize", "pyannote/speaker-diarization-3.1", "", accel_device,
           "diarize", time.perf_counter() - t0, audio_s)


def main():
    audio_s = audio_seconds()
    print(f"audio: {AUDIO}  ({audio_s / 60:.1f} min)")
    print(f"whisper model device: {WHISPER_DEVICE}   accel device: {ACCEL_DEVICE}\n")

    for model_name in ["tiny", "small", "medium", "large-v3"]:
        bench_faster_whisper(model_name, audio_s)
        print()

    # "cpu" first as the baseline, then whatever's actually fastest on this
    # machine -- isolates the accel-device effect at a cheap model size
    # before spending time on the full-size model.
    for model_name, accel_device in [
        ("small", "cpu"),
        ("small", ACCEL_DEVICE),
        ("large-v3", ACCEL_DEVICE),
    ]:
        bench_whisperx(model_name, accel_device, audio_s)
        print()

    # Standalone diarization -- the cost you pay separately for any engine
    # without its own (faster_whisper, or whisperx used transcript-only).
    for accel_device in ["cpu", ACCEL_DEVICE]:
        bench_diarize_standalone(accel_device, audio_s)
        print()

    print(f"Results: {RESULTS}")


if __name__ == "__main__":
    main()
