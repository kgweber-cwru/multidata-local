"""Stage: asr — transcription + word timing + diarization (doc §6).

Runs in the **md-speech** env. Writes one JSON per case to
`data/transcripts/<case_id>/<engine>.json`; that JSON is the single source of
truth for §7 (ELAN) and §8 (benchmarking).

Engines (doc §6: "keep those code paths behind a --engine flag"):

- ``whisperx`` — **the default and the target path**: whisper + wav2vec2 word
  alignment + pyannote diarization, all running locally on this machine. Written
  from the doc §6 sketch; not yet run here, so expect first-run friction (model
  downloads, HF gating).
- ``faster_whisper`` — the reproducible benchmark engine, with the §6a
  anti-hallucination decoding hygiene applied. No diarization; pair with
  `diarize.py` if you need speakers.
- ``suite`` — Transcription Suite HTTP server (parakeet + sortformer), promoted
  from `notebooks/transcription_suite.ipynb`. This is the *old machine's* path
  and is **not** the intended pipeline; it is retained only as a benchmark
  comparator, since it produced the transcripts we already have. Requires that
  server to be running.

`elan.build_eaf` accepts any of these outputs (it reads root `words` or flattens
`segments[].words`).
"""
import json
from pathlib import Path

SUITE_URL = "http://localhost:9786/api/"

# Seeded domain terms soften the "nurse -> nerds" substitution class (doc §6a).
# Treat this as an experimental variable in the benchmark, not a fixed constant.
DEFAULT_PROMPT = (
    "Clinical consultation transcript between a healthcare provider and a patient "
    "discussing medical symptoms, history, diagnosis, and treatment plan, followed "
    "by feedback between the provider and their instructor. Formal medical "
    "terminology is used throughout."
)


def transcribe_suite(audio_path, base_url=SUITE_URL, expected_speakers=4,
                     prompt=DEFAULT_PROMPT, language="en"):
    """POST audio to a running Transcription Suite server -> result dict."""
    import requests

    data = {
        "language": language,
        "translation_enabled": "false",
        "word_timestamps": "true",
        "diarization": "true",
        "expected_speakers": expected_speakers,
        "prompt": prompt,
    }
    with open(audio_path, "rb") as f:
        response = requests.post(base_url + "transcribe/audio",
                                 data=data, files={"file": f})
    response.raise_for_status()
    return response.json()


def transcribe_whisperx(audio_path, model_name="large-v3", device="cpu", batch_size=8):
    """Whisper + word alignment + diarization in one pass (doc §6 sketch)."""
    import whisperx

    from multidata.hf_auth import hf_token

    audio = whisperx.load_audio(str(audio_path))

    model = whisperx.load_model(model_name, device, compute_type="int8")
    result = model.transcribe(audio, batch_size=batch_size)

    align_model, meta = whisperx.load_align_model(result["language"], device)
    result = whisperx.align(result["segments"], align_model, meta, audio, device)

    dia = whisperx.diarize.DiarizationPipeline(token=hf_token(), device=device)
    result = whisperx.assign_word_speakers(dia(str(audio_path)), result)

    return result


def transcribe_faster_whisper(audio_path, model_name="large-v3", prompt=DEFAULT_PROMPT):
    """Pinned-config faster-whisper with §6a decoding hygiene. No diarization."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        vad_filter=True,                    # biggest single lever on hallucination
        condition_on_previous_text=False,   # stops hallucination loops
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        word_timestamps=True,
        initial_prompt=prompt,
    )
    return {
        "language": info.language,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [
                    {"word": w.word, "start": w.start, "end": w.end, "score": w.probability}
                    for w in (s.words or [])
                ],
            }
            for s in segments
        ],
    }


ENGINES = {
    "whisperx": transcribe_whisperx,
    "faster_whisper": transcribe_faster_whisper,
    "suite": transcribe_suite,
}

DEFAULT_ENGINE = "whisperx"


def transcribe(audio_path, out_path=None, engine=DEFAULT_ENGINE, **kwargs):
    """Run one engine and persist its JSON."""
    if engine not in ENGINES:
        raise ValueError(f"Unknown engine {engine!r}; pick one of {sorted(ENGINES)}")

    result = ENGINES[engine](audio_path, **kwargs)

    if out_path is not None:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=4)
        print(f"Wrote {out_path}  (engine={engine})")
    return result
