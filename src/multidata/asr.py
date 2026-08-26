"""Stage: asr — transcription + word timing + diarization (doc §6).

Runs in the **md-speech** env. Writes one JSON per (case, camera, engine,
model) to `data/transcripts/<case_id>/<camera>_<engine>_<model>.json`, plus a
plain-text sibling (`.txt`) for a quick human read. Every output is
word-level speaker-labeled (`segments[].words[].speaker`) regardless of
engine — that's the single source of truth for §7 (ELAN, which only ever
reads word-level `word/start/end/speaker`) and §8 (benchmarking).

Engines (doc §6: "keep those code paths behind a --engine flag"):

- ``faster_whisper`` — **the default**: CT2 backend, fast CPU int8, with the
  §6a anti-hallucination decoding hygiene applied. Doesn't diarize on its
  own — `transcribe()` below merges in a `diarize.diarize()` result (reusing
  an existing RTTM if one's already on disk, writing one otherwise) via
  whisperx's own engine-agnostic `assign_word_speakers`. Confirmed empirically
  (benchmarks/timing_bench.py) to be slightly *faster* end-to-end than
  whisperx at the same model size, not just simpler.
- ``whisperx`` — bundles transcribe + wav2vec2 word alignment + pyannote
  diarization in one call. Available for testing/comparison via `--engine`;
  word-level alignment is arguably higher-quality timing than a CT2 model's
  native timestamps, which is the main reason to reach for it.
- ``whisperx_disfluent`` — same as ``whisperx``, decode-tuned to *keep*
  disfluencies instead of suppressing hallucination
  (docs/asr_provider_implementation_plan.md Phase 0.3/3.3), since this
  project's policy (docs/transcription_standards.md §1) is to keep them and
  the default engines are tuned the opposite way (§6a). Experimental — its own
  docstring carries the caveats.
- ``suite`` — Transcription Suite HTTP server (parakeet + sortformer), promoted
  from `notebooks/transcription_suite.ipynb`. This is the *old machine's* path
  and is **not** the intended pipeline; it is retained only as a benchmark
  comparator, since it produced the transcripts we already have. Requires that
  server to be running. Already diarizes itself (server-side), like whisperx.

Default model is `medium` and default language is `en` (skip auto-detect —
faster and safer for this fixed-language clinical corpus). Override either
via `run_stage.py --model/--language` for testing.
"""
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

SUITE_URL = "http://localhost:9786/api/"

DEFAULT_MODEL = "medium"
DEFAULT_LANGUAGE = "en"

# Seeded domain terms soften the "nurse -> nerds" substitution class (doc §6a).
# Treat this as an experimental variable in the benchmark, not a fixed constant.
DEFAULT_PROMPT = (
    "Clinical consultation transcript between a healthcare provider and a patient "
    "discussing medical symptoms, history, diagnosis, and treatment plan, followed "
    "by feedback between the provider and their instructor. Formal medical "
    "terminology is used throughout."
)


def transcribe_suite(audio_path, base_url=SUITE_URL, expected_speakers=4,
                     prompt=DEFAULT_PROMPT, language=DEFAULT_LANGUAGE):
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


def transcribe_whisperx(audio_path, model_name=DEFAULT_MODEL, device=None, compute_type=None,
                         align_device=None, diarize_device=None, batch_size=8,
                         language=DEFAULT_LANGUAGE):
    """Whisper + word alignment + diarization in one pass (doc §6 sketch).

    `device`/`compute_type` govern the Whisper model's own decode step
    (ctranslate2, shared with `transcribe_faster_whisper`): default to
    `multidata.device.best_ct2_device`/`best_ct2_compute_type`, which only
    ever choose "cpu"/"cuda" (never "mps" -- ctranslate2 has no Metal
    backend). On this Mac that resolves to cpu/int8, identical to the old
    hardcoded default; on an NVIDIA box the same call resolves to cuda/float16
    with no code change -- that's the whole point of routing through
    `device.py` instead of hardcoding here.

    `align_device`/`diarize_device` govern the wav2vec2 aligner and pyannote
    diarizer instead -- ordinary torch models, independent of the Whisper
    decode step, and mps-capable via `multidata.device.best_torch_device`.
    `diarize_device` defaults to that (confirmed empirically ~7x faster under
    CoreML/MPS than CPU on this machine). `align_device` defaults to `device`
    (i.e. stays on the ct2 device) rather than also defaulting to the torch
    fast path -- unlike diarization, alignment-on-`mps` hasn't been
    benchmarked cleanly here yet (only tested under heavy CPU contention from
    a concurrent run, which isn't a fair read). Pass `align_device="mps"`
    explicitly to try it.
    """
    import whisperx

    from multidata.device import best_ct2_compute_type, best_ct2_device, best_torch_device
    from multidata.hf_auth import hf_token

    device = device or best_ct2_device()
    compute_type = compute_type or best_ct2_compute_type(device)
    align_device = align_device or device
    diarize_device = diarize_device or best_torch_device()

    audio = whisperx.load_audio(str(audio_path))

    model = whisperx.load_model(model_name, device, compute_type=compute_type, language=language)
    result = model.transcribe(audio, batch_size=batch_size, language=language)

    align_model, meta = whisperx.load_align_model(result["language"], align_device)
    result = whisperx.align(result["segments"], align_model, meta, audio, align_device)

    dia = whisperx.diarize.DiarizationPipeline(token=hf_token(), device=diarize_device)
    result = whisperx.assign_word_speakers(dia(str(audio_path)), result)

    return result


# Written IN the register we want back, not a clinical-register description of
# it -- the theory (docs/asr_provider_implementation_plan.md Phase 0.3) is that
# Whisper's prompt-following is a style prior as much as a vocabulary hint, so
# a prompt that itself contains fillers, a self-correction, and a backchannel
# should pull decoding toward reproducing that register rather than cleaning
# it up. Unverified until scored against gold -- that scoring *is* Phase 0.3.
DISFLUENCY_PROMPT = (
    "Um, so, uh, the patient came in and, uh, she was — she was saying that, "
    "um, her chest kind of hurt, you know, on and off. Mm-hmm. And, uh, the "
    "doctor asked a few more questions about her history."
)


def transcribe_whisperx_disfluent(audio_path, model_name=DEFAULT_MODEL, device=None,
                                   compute_type=None, align_device=None, diarize_device=None,
                                   batch_size=8, language=DEFAULT_LANGUAGE,
                                   prompt=DISFLUENCY_PROMPT, condition_on_previous_text=True):
    """`transcribe_whisperx`, but decode-tuned to *keep* disfluencies instead
    of suppressing hallucination (implementation plan Phase 0.3 / 3.3) -- the
    alternate provider this project's disfluency-preservation policy
    (docs/transcription_standards.md §1: "keep disfluencies") needs to test
    against, since `transcribe_faster_whisper`'s decoding hygiene (doc §6a) is
    tuned in the opposite direction.

    Differs from the default whisperx path in exactly two settings, passed
    through whisperx's `asr_options` (confirmed against the installed
    whisperx's `whisperx/asr.py`: `load_model(..., asr_options={...})` merges
    into its `TranscriptionOptions`, which has `initial_prompt` and
    `condition_on_previous_text` fields -- re-check this if whisperx is
    upgraded, since `env/speech.yml` doesn't pin its version):

    - `condition_on_previous_text=True` -- whisperx/ctranslate2's own default,
      which `transcribe_faster_whisper` deliberately turns *off* to stop
      hallucination loops. Leaving it on is also what lets a disfluent
      register set by `prompt` propagate across segments instead of resetting
      each segment. **This is a real tradeoff, not a free upgrade**: it's also
      the main lever that causes repetition loops on bad audio. Sweep against
      `condition_on_previous_text=False` explicitly -- don't assume this
      config is safe on a noisy case just because it helped on a clean one.
    - `prompt=DISFLUENCY_PROMPT` in place of `asr.DEFAULT_PROMPT`'s clean
      clinical register.

    Everything else -- model, alignment, diarization, device selection -- is
    identical to `transcribe_whisperx`.
    """
    import whisperx

    from multidata.device import best_ct2_compute_type, best_ct2_device, best_torch_device
    from multidata.hf_auth import hf_token

    device = device or best_ct2_device()
    compute_type = compute_type or best_ct2_compute_type(device)
    align_device = align_device or device
    diarize_device = diarize_device or best_torch_device()

    audio = whisperx.load_audio(str(audio_path))

    asr_options = {
        "initial_prompt": prompt,
        "condition_on_previous_text": condition_on_previous_text,
    }
    model = whisperx.load_model(model_name, device, compute_type=compute_type,
                                 language=language, asr_options=asr_options)
    result = model.transcribe(audio, batch_size=batch_size, language=language)

    align_model, meta = whisperx.load_align_model(result["language"], align_device)
    result = whisperx.align(result["segments"], align_model, meta, audio, align_device)

    dia = whisperx.diarize.DiarizationPipeline(token=hf_token(), device=diarize_device)
    result = whisperx.assign_word_speakers(dia(str(audio_path)), result)

    return result


def transcribe_faster_whisper(audio_path, model_name=DEFAULT_MODEL, prompt=DEFAULT_PROMPT,
                               language=DEFAULT_LANGUAGE, device=None, compute_type=None):
    """Pinned-config faster-whisper with §6a decoding hygiene.

    No diarization of its own -- `transcribe()` merges one in afterward, the
    same way for every non-self-diarizing engine.

    `device`/`compute_type` default to `multidata.device.best_ct2_device`/
    `best_ct2_compute_type` -- cpu/int8 on this Mac (unchanged from the old
    hardcoded default), cuda/float16 with no code change on an NVIDIA box.
    """
    from faster_whisper import WhisperModel

    from multidata.device import best_ct2_compute_type, best_ct2_device

    device = device or best_ct2_device()
    compute_type = compute_type or best_ct2_compute_type(device)

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
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
    "faster_whisper": transcribe_faster_whisper,
    "whisperx": transcribe_whisperx,
    "whisperx_disfluent": transcribe_whisperx_disfluent,
    "suite": transcribe_suite,
}

# Engines that already return word-level `speaker` labels on their own;
# everything else gets diarization merged in by `transcribe()`.
DIARIZES_INTERNALLY = {"whisperx", "whisperx_disfluent", "suite"}

# Engines whose function accepts `diarize_device` directly (the whisperx
# family) rather than getting one merged in by `_diarize_and_merge` -- kept as
# its own set rather than reusing DIARIZES_INTERNALLY because `suite` also
# diarizes internally but takes no such parameter (it's server-side).
TAKES_DIARIZE_DEVICE = {"whisperx", "whisperx_disfluent"}

DEFAULT_ENGINE = "faster_whisper"


def _diarize_and_merge(result, audio_path, rttm_path=None, device=None,
                        num_speakers=None, min_speakers=None, max_speakers=None):
    """Attach word-level `speaker` labels to a transcript that doesn't have
    them yet, reusing `rttm_path` if it already exists rather than paying for
    pyannote twice (doc §6)."""
    from multidata import diarize as diarize_mod
    from whisperx.diarize import assign_word_speakers

    if rttm_path is not None and Path(rttm_path).exists():
        log.info("reusing existing diarization: %s", rttm_path)
        annotation = diarize_mod.load_rttm(rttm_path)
    else:
        annotation = diarize_mod.diarize(
            audio_path, out_rttm=rttm_path, device=device,
            num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers,
        )

    diarize_df = diarize_mod.annotation_to_dataframe(annotation)
    return assign_word_speakers(diarize_df, result)


def _format_timestamp(seconds):
    if seconds is None:
        return "--:--:--"
    return time.strftime("%H:%M:%S", time.gmtime(seconds))


def write_plain_text(result, out_path):
    """Quick-read speaker-turn transcript: consecutive same-speaker words
    merged into one timestamped line. For skimming, not parsing -- use the
    JSON for anything downstream."""
    lines = []
    current_speaker = None
    current_words = []
    current_start = None

    def flush():
        if current_words:
            text = " ".join(w.strip() for w in current_words if w.strip())
            lines.append(f"[{_format_timestamp(current_start)}] {current_speaker}: {text}")

    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            speaker = word.get("speaker", "UNKNOWN")
            if speaker != current_speaker:
                flush()
                current_speaker = speaker
                current_words = []
                current_start = word.get("start")
            current_words.append(word.get("word", ""))
    flush()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n" if lines else "")
    return out_path


def transcribe(audio_path, out_path=None, engine=DEFAULT_ENGINE,
               diarize_rttm=None, diarize_device=None,
               num_speakers=None, min_speakers=None, max_speakers=None,
               **kwargs):
    """Run one engine, merge in speaker labels (unless the engine already
    provides them), and persist the JSON plus a plain-text sibling.

    `diarize_rttm`, if given, is read if it already exists (reuse) or written
    fresh otherwise (cache for next time / for DER scoring) -- see
    `diarize.diarize`. Ignored for engines in `DIARIZES_INTERNALLY`, which
    still take `diarize_device` themselves (forwarded via `kwargs` for the
    whisperx family, `TAKES_DIARIZE_DEVICE`).
    """
    if engine not in ENGINES:
        raise ValueError(f"Unknown engine {engine!r}; pick one of {sorted(ENGINES)}")

    if engine in TAKES_DIARIZE_DEVICE:
        kwargs.setdefault("diarize_device", diarize_device)
        result = ENGINES[engine](audio_path, **kwargs)
    else:
        result = ENGINES[engine](audio_path, **kwargs)
        if engine not in DIARIZES_INTERNALLY:
            result = _diarize_and_merge(result, audio_path, diarize_rttm, diarize_device,
                                        num_speakers, min_speakers, max_speakers)

    result.setdefault("engine", engine)

    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        text_path = write_plain_text(result, out_path.with_suffix(".txt"))
        log.info("wrote %s and %s  (engine=%s)", out_path, text_path, engine)
    return result
