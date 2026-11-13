"""One way to transcribe a recording, whichever engine does it.

    from multidata import transcribers

    t = transcribers.transcribe_recording("261456", "assemblyai", speakers_expected=3)
    t.record    # normalized record (docs/asr_provider_spec.md §4); params in its provenance
    t.native    # the vendor's response, verbatim
    t.params    # every parameter that produced it, after defaults
    t.save()    # data/transcripts/261456/<camera>_assemblyai_universal-3-5-pro{.json,.raw.json}

`transcribers.params("elevenlabs")` lists what an engine accepts and its
defaults. A parameter the engine doesn't take is a TypeError before anything
runs. Cloud engines refuse a recording whose manifest row lacks
`cloud_release`, before its audio is read (spec §9).

Local engines are `asr.ENGINES` (the Whisper family). Cloud credentials come
from `.env`: `ELEVENLABS_API_KEY`, `ASSEMBLYAI_API_KEY`, and for Google
`GOOGLE_CLOUD_PROJECT`, `GOOGLE_STT_BUCKET` and application-default
credentials (`gcloud auth application-default login`, or
`GOOGLE_APPLICATION_CREDENTIALS`).
"""
import dataclasses

from multidata import asr
from multidata.paths import MANIFEST_PATH

from .assemblyai import AssemblyAITranscriber
from .base import CloudReleaseError, Transcriber, Transcription
from .elevenlabs import ElevenLabsTranscriber
from .google import GoogleTranscriber
from .whisper import WhisperTranscriber, engine_defaults

CLOUD = {t.engine: t for t in (ElevenLabsTranscriber, AssemblyAITranscriber, GoogleTranscriber)}

__all__ = ["CLOUD", "CloudReleaseError", "Transcriber", "Transcription", "engines",
           "get_transcriber", "params", "transcribe_recording"]


def engines():
    return sorted(set(asr.ENGINES) | set(CLOUD))


def get_transcriber(engine, **params):
    if engine in asr.ENGINES:
        return WhisperTranscriber(engine, **params)
    if engine in CLOUD:
        return CLOUD[engine](**params)
    raise ValueError(f"unknown engine {engine!r}; pick one of {engines()}")


def params(engine):
    """{parameter: default} that `engine` accepts."""
    if engine in asr.ENGINES:
        return engine_defaults(engine)
    if engine in CLOUD:
        return {f.name: f.default for f in dataclasses.fields(CLOUD[engine].Params)}
    raise ValueError(f"unknown engine {engine!r}; pick one of {engines()}")


def transcribe_recording(recording_id, engine, *, audio_path=None, manifest_path=MANIFEST_PATH,
                         **engine_params):
    """Transcribe one recording with one engine -> `Transcription`."""
    return get_transcriber(engine, **engine_params).transcribe(
        recording_id, manifest_path=manifest_path, audio_path=audio_path)
