"""Gemini 3.5 Transcribe on Vertex AI (Google Cloud's "Agent Platform"),
through `generate_content` with an `AudioTranscriptionConfig`.

Checked against docs.cloud.google.com/gemini-enterprise-agent-platform/models/
gemini/3-5-transcribe and aistudio.google.com/docs/transcribe on 2026-11-16,
and against google-genai 3.3.0's types. **Not yet run against the live API.**

What shapes this adapter:

- Vertex only (`genai.Client(enterprise=True, ...)`, the project's
  application-default credentials), never the AI Studio Gemini API, which
  takes an API key and sits outside the project's agreements.
- `mode="VERBATIM"`: SMART mode drops speaker labels and word timestamps.
- Files are capped at 15 minutes on Vertex (the AI Studio page says 30 with
  diarization or timestamps on, and that longer audio is silently
  truncated). `chunk_s` sends 14-minute pieces through the shared chunker,
  which links the speakers across them (`chunking.py`).
- Audio is staged in `GOOGLE_STT_BUCKET` and passed by URI, as for Chirp,
  then deleted (`delete_staged`).
- Diarization, word timestamps and 3+ speaker attribution are all marked
  Experimental by Google, and word timestamps "may degrade" accuracy.
- No word confidence is returned, so `score` stays None.
- Pricing, retention and BAA coverage aren't on the model page: check the
  BAA's covered-services list before sending recordings beyond gold.
"""
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import CloudTranscriber, SpeakerLabels, segments_from_words, word
from .google import _offset

MIME = {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg"}


@dataclass
class GeminiParams:
    model: str = "gemini-3.5-transcribe"
    location: str = "us"                     # us | eu | global
    language_codes: tuple = ("en-US",)
    diarization: bool = True
    word_timestamp: bool = True
    mode: str = "VERBATIM"                   # SMART drops speakers and timestamps
    custom_vocabulary: Optional[tuple] = None  # up to 1000; vendor suggests <= 100
    chunk_s: Optional[float] = 840           # 14 min, under Vertex's 15-minute file cap
    chunk_overlap_s: float = 60.0
    delete_staged: bool = True


class GeminiTranscriber(CloudTranscriber):
    engine = "gemini"
    Params = GeminiParams
    env_keys = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_STT_BUCKET")
    packages = ("google-genai", "google-cloud-storage")

    def check_params(self):
        p = self.params
        if p.mode not in ("VERBATIM", "SMART"):
            raise ValueError("mode is VERBATIM or SMART")
        if p.mode == "SMART" and (p.diarization or p.word_timestamp):
            raise ValueError("SMART mode returns no speaker labels or word timestamps")
        if p.custom_vocabulary and len(p.custom_vocabulary) > 1000:
            raise ValueError("Gemini Transcribe takes at most 1000 custom vocabulary terms")
        if p.chunk_s is not None and not 0 <= p.chunk_overlap_s < p.chunk_s / 2:
            raise ValueError("need 0 <= chunk_overlap_s < chunk_s / 2")

    def transcription_config(self):
        from google.genai import types

        p = self.params
        config = dict(language_codes=list(p.language_codes), diarization=p.diarization,
                      word_timestamp=p.word_timestamp, mode=p.mode)
        if p.custom_vocabulary:
            config["custom_vocabulary"] = list(p.custom_vocabulary)
        return types.AudioTranscriptionConfig(**config)

    def call(self, audio_path, recording_id, camera):
        from google import genai
        from google.cloud import storage
        from google.genai import types

        p = self.params
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        bucket = storage.Client(project=project).bucket(os.environ["GOOGLE_STT_BUCKET"])
        suffix = Path(audio_path).suffix
        blob = bucket.blob(f"multidata-stt/{recording_id}/{uuid.uuid4().hex[:8]}{suffix}")
        blob.upload_from_filename(str(audio_path))
        try:
            client = genai.Client(enterprise=True, project=project, location=p.location)
            response = client.models.generate_content(
                model=p.model,
                contents=[types.Part.from_uri(file_uri=f"gs://{bucket.name}/{blob.name}",
                                              mime_type=MIME.get(suffix, "audio/wav"))],
                config=types.GenerateContentConfig(
                    audio_transcription_config=self.transcription_config()))
        finally:
            if p.delete_staged:
                blob.delete()
        native = response.model_dump(mode="json", exclude_none=True)
        return native, {"request_id": native.get("response_id"),
                        "model_returned": native.get("model_version")}

    def normalize(self, native):
        speakers = SpeakerLabels()
        words, language = [], "en"
        for part in _parts(native):
            tx = part.get("audio_transcription")
            if not tx:
                continue
            language = (tx.get("language_code") or language).split("-")[0]
            speaker = speakers(tx.get("speaker_label"))
            if tx.get("words"):
                for w in tx["words"]:
                    words.append(word(w.get("word", ""), _offset(w.get("start_offset")),
                                      _offset(w.get("end_offset")), speaker=speaker))
            elif tx.get("text"):   # no word timing: one untimed word per token
                for t in tx["text"].split():
                    words.append(word(t, None, None, speaker=speaker))
        timed = any(w["start"] is not None for w in words)
        return {"language": language, "segments": segments_from_words(words),
                "word_timing": timed}


def _parts(native):
    candidates = native.get("candidates") or []
    return ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
