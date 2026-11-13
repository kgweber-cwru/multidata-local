"""AssemblyAI pre-recorded transcription: upload, submit, poll, delete.

Checked against assemblyai.com/docs/api-reference/transcripts/submit,
/llms/models.md and /md/pricing on 2026-11-13. Pre-recorded models:
`universal-3-5-pro` (18 languages; prompting, keyterms up to 1000) and
`universal-2` (99+ languages; keyterms up to 200). One model per run here
(`speech_models: [model]`): a fallback list would let the vendor change model
mid-benchmark, and `speech_model_used` is recorded either way.

- `disfluencies=True` keeps fillers. The docs disagree on whether
  universal-3-5-pro honours it (the submit schema says yes; models.md says it
  is ignored there and to prompt instead). The response echoes the flag, but
  that doesn't prove it worked; check the filler counts.
- `prompt` and `keyterms_prompt` are mutually exclusive on pre-recorded audio
  according to models.md; enforced here.
- `format_text=False` asks for unformatted text (spec §6); `punctuate` stays
  True because diarization requires it.
- Nothing in the API opts a request out of retention or training. Instead,
  `delete_after=True` (the default) deletes the transcript and the uploaded
  audio from AssemblyAI once the response is saved locally.
- Times come back in milliseconds; the normalized record uses seconds.
"""
import os
import time
from dataclasses import dataclass
from typing import Optional

from .base import CloudTranscriber, SpeakerLabels, segments_from_words, word

US = "https://api.assemblyai.com"
EU = "https://api.eu.assemblyai.com"


@dataclass
class AssemblyAIParams:
    model: str = "universal-3-5-pro"             # sent as speech_models: [model]
    language_code: Optional[str] = "en"
    speaker_labels: bool = True
    speakers_expected: Optional[int] = None
    min_speakers_expected: Optional[int] = None
    max_speakers_expected: Optional[int] = None
    disfluencies: bool = True
    format_text: bool = False
    punctuate: bool = True
    keyterms_prompt: Optional[tuple] = None
    prompt: Optional[str] = None                 # universal-3-5-pro only
    domain: Optional[str] = None                 # "medical-v1" = Medical Mode (+$0.15/h)
    temperature: Optional[float] = None          # universal-3-5-pro only, 0-1
    remove_audio_tags: Optional[str] = None      # all | speaker; vendor default strips tags
    base_url: str = US
    delete_after: bool = True
    poll_interval_s: float = 5.0
    timeout_s: int = 7200


class AssemblyAITranscriber(CloudTranscriber):
    engine = "assemblyai"
    Params = AssemblyAIParams
    env_keys = ("ASSEMBLYAI_API_KEY",)
    packages = ("requests",)

    def check_params(self):
        p = self.params
        if p.prompt and p.keyterms_prompt:
            raise ValueError("prompt and keyterms_prompt can't be combined on pre-recorded audio")
        has_range = p.min_speakers_expected is not None or p.max_speakers_expected is not None
        if p.speakers_expected is not None and has_range:
            raise ValueError("give speakers_expected or a min/max range, not both")
        if (p.speakers_expected is not None or has_range) and not p.speaker_labels:
            raise ValueError("speaker counts need speaker_labels=True")
        if p.speaker_labels and not p.punctuate:
            raise ValueError("speaker_labels needs punctuate=True")
        limit = 1000 if p.model == "universal-3-5-pro" else 200
        if p.keyterms_prompt and len(p.keyterms_prompt) > limit:
            raise ValueError(f"{p.model} takes at most {limit} keyterms")
        if p.model != "universal-3-5-pro" and (p.prompt or p.temperature is not None):
            raise ValueError("prompt and temperature are universal-3-5-pro only")

    def request_body(self, audio_url):
        p = self.params
        body = {
            "audio_url": audio_url,
            "speech_models": [p.model],
            "speaker_labels": p.speaker_labels,
            "disfluencies": p.disfluencies,
            "format_text": p.format_text,
            "punctuate": p.punctuate,
        }
        for name in ("language_code", "speakers_expected", "prompt", "domain",
                     "temperature", "remove_audio_tags"):
            value = getattr(p, name)
            if value is not None:
                body[name] = value
        if p.keyterms_prompt:
            body["keyterms_prompt"] = list(p.keyterms_prompt)
        if p.min_speakers_expected is not None or p.max_speakers_expected is not None:
            body["speaker_options"] = {k: v for k, v in (
                ("min_speakers_expected", p.min_speakers_expected),
                ("max_speakers_expected", p.max_speakers_expected)) if v is not None}
        return body

    def call(self, audio_path, recording_id, camera):
        import requests

        p = self.params
        session = requests.Session()
        session.headers["authorization"] = os.environ["ASSEMBLYAI_API_KEY"]

        with open(audio_path, "rb") as f:
            upload = session.post(f"{p.base_url}/v2/upload", data=f,
                                  headers={"Content-Type": "application/octet-stream"},
                                  timeout=(30, 1800))
        _check(upload)
        submitted = session.post(f"{p.base_url}/v2/transcript",
                                 json=self.request_body(upload.json()["upload_url"]), timeout=60)
        _check(submitted)
        transcript_id = submitted.json()["id"]

        try:
            native = self._poll(session, transcript_id)
        finally:
            deleted = p.delete_after and _delete(session, p.base_url, transcript_id)

        notes = ["deleted from AssemblyAI after download" if deleted
                 else "left on AssemblyAI's servers"]
        warnings = (native.get("metadata") or {}).get("warnings") or []
        notes += [f"vendor warning: {w.get('message')}" for w in warnings]
        return native, {
            "request_id": transcript_id,
            "model_returned": native.get("speech_model_used"),
            "audio_duration_s": native.get("audio_duration"),
            "notes": "; ".join(notes),
        }

    def _poll(self, session, transcript_id):
        p = self.params
        url = f"{p.base_url}/v2/transcript/{transcript_id}"
        deadline = time.monotonic() + p.timeout_s
        while time.monotonic() < deadline:
            response = session.get(url, timeout=60)
            if response.status_code == 429:
                time.sleep(float(response.headers.get("Retry-After", 30)))
                continue
            _check(response)
            native = response.json()
            if native["status"] == "completed":
                return native
            if native["status"] == "error":
                raise RuntimeError(f"AssemblyAI transcript {transcript_id} failed: {native.get('error')}")
            time.sleep(p.poll_interval_s)
        raise TimeoutError(f"AssemblyAI transcript {transcript_id} not done after {p.timeout_s} s")

    def normalize(self, native):
        speakers = SpeakerLabels()

        def words_of(items):
            return [word(w["text"], _s(w.get("start")), _s(w.get("end")),
                         score=w.get("confidence"), speaker=speakers(w.get("speaker")))
                    for w in items or []]

        if native.get("utterances"):
            segments = []
            for u in native["utterances"]:
                words = words_of(u.get("words"))
                segment = {"start": _s(u["start"]), "end": _s(u["end"]), "text": u["text"],
                           "words": words}
                if words and words[0].get("speaker"):
                    segment["speaker"] = words[0]["speaker"]
                segments.append(segment)
        else:
            segments = segments_from_words(words_of(native.get("words")))
        language = (native.get("language_code") or "en").split("_")[0]
        return {"language": language, "segments": segments}


def _s(ms):
    return None if ms is None else ms / 1000


def _check(response):
    if not response.ok:
        raise RuntimeError(f"AssemblyAI {response.status_code}: {response.text[:500]}")


def _delete(session, base_url, transcript_id):
    try:
        return session.delete(f"{base_url}/v2/transcript/{transcript_id}", timeout=60).ok
    except Exception:
        return False
