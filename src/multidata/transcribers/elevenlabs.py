"""ElevenLabs Scribe, batch speech-to-text (`POST /v1/speech-to-text`).

Checked against elevenlabs.io/docs/api-reference/speech-to-text/convert and
/docs/models on 2026-11-13. Models for this endpoint: `scribe_v3` (the
vendor's recommendation for recorded audio), `scribe_v2`, `scribe_v2_medical`
(HIPAA-eligible, BAA, same API). `scribe_v1` is deprecated.

One synchronous request: files up to 5 GB / 15 h, and the vendor chunks
anything over 8 minutes internally, so a long recording just takes a while.

- `enable_logging=False` asks for zero-retention mode. The docs say only
  enterprise accounts may use it, so expect an error on any other account
  rather than switching it on quietly (spec §9).
- `no_verbatim` stays False: it strips fillers and false starts, which the
  transcription standards keep.
- Audio events come back as words of type `audio_event`, such as
  "(laughter)". The normalized record writes them in square brackets so the
  scorer's bracket rule drops them (spec §6); the native response keeps them
  as sent.
- There is no per-word confidence, only `logprob`; `score` is exp(logprob).
- The response doesn't name the model that ran, so `model_returned` is None.
"""
import math
import os
from dataclasses import dataclass
from typing import Optional

from .base import CloudTranscriber, SpeakerLabels, segments_from_words, word

URL = "https://api.elevenlabs.io/v1/speech-to-text"
KEYTERM_FORBIDDEN = set("<>{}[]\\")


@dataclass
class ElevenLabsParams:
    model: str = "scribe_v3"                     # sent as model_id
    language_code: Optional[str] = "en"
    diarize: bool = True
    num_speakers: Optional[int] = None           # up to 32; None lets the model decide
    diarization_threshold: Optional[float] = None
    timestamps_granularity: str = "word"         # word | character | none
    tag_audio_events: bool = True
    keyterms: Optional[tuple] = None             # up to 1000; +20% cost
    no_verbatim: bool = False
    numbers_format: Optional[str] = None         # digits | words (scribe_v3 only)
    temperature: Optional[float] = None          # 0-2; vendor default ~0
    seed: Optional[int] = None
    enable_logging: bool = False                 # False = zero-retention mode
    base_url: str = URL                          # or a data-residency host
    timeout_s: int = 3600


class ElevenLabsTranscriber(CloudTranscriber):
    engine = "elevenlabs"
    Params = ElevenLabsParams
    env_keys = ("ELEVENLABS_API_KEY",)
    packages = ("requests",)

    def check_params(self):
        p = self.params
        if p.diarization_threshold is not None and (not p.diarize or p.num_speakers is not None):
            raise ValueError("diarization_threshold needs diarize=True and num_speakers=None")
        if p.numbers_format is not None and p.model != "scribe_v3":
            raise ValueError("numbers_format is scribe_v3 only")
        if p.keyterms:
            if len(p.keyterms) > 1000:
                raise ValueError("ElevenLabs takes at most 1000 keyterms")
            for t in p.keyterms:
                if len(t) > 50 or len(t.split()) > 5 or set(t) & KEYTERM_FORBIDDEN:
                    raise ValueError(f"keyterm {t!r}: at most 50 characters and 5 words, "
                                     "and none of <>{}[]\\")

    def call(self, audio_path, recording_id, camera):
        import requests

        p = self.params
        form = {
            "model_id": p.model,
            "diarize": _bool(p.diarize),
            "timestamps_granularity": p.timestamps_granularity,
            "tag_audio_events": _bool(p.tag_audio_events),
            "no_verbatim": _bool(p.no_verbatim),
        }
        for name in ("language_code", "num_speakers", "diarization_threshold",
                     "numbers_format", "temperature", "seed"):
            value = getattr(p, name)
            if value is not None:
                form[name] = str(value)
        if p.keyterms:
            form["keyterms"] = list(p.keyterms)

        with open(audio_path, "rb") as f:
            response = requests.post(
                p.base_url, params={"enable_logging": _bool(p.enable_logging)},
                headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                data=form, files={"file": (audio_path.name, f)}, timeout=(30, p.timeout_s))
        if not response.ok:
            raise RuntimeError(f"ElevenLabs {response.status_code}: {response.text[:500]}")
        native = response.json()
        return native, {
            "request_id": native.get("transcription_id") or response.headers.get("request-id"),
            "model_returned": None,
            "audio_duration_s": native.get("audio_duration_secs"),
        }

    def normalize(self, native):
        speakers = SpeakerLabels()
        words = []
        for w in native.get("words", []):
            if w.get("type") == "spacing":
                continue
            text = w["text"].strip()
            if w.get("type") == "audio_event":
                text = "[" + text.strip("()[]") + "]"
            logprob = w.get("logprob")
            words.append(word(text, w.get("start"), w.get("end"),
                              score=math.exp(logprob) if logprob is not None else None,
                              speaker=speakers(w.get("speaker_id"))))
        language = native.get("language_code") or ""
        return {"language": {"eng": "en"}.get(language, language),
                "segments": segments_from_words(words),
                "word_timing": self.params.timestamps_granularity != "none"}


def _bool(value):
    return "true" if value else "false"
