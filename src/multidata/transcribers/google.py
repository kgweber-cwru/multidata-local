"""Google Cloud Speech-to-Text v2, BatchRecognize.

Checked against docs.cloud.google.com/speech-to-text/docs/models/chirp-3,
/batch-recognize, /multiple-voices and /data-logging on 2026-11-13.
**Not yet run against the live API.** The Python field names below follow
the v2 proto (`RecognitionFeatures`, `SpeakerDiarizationConfig`, `WordInfo`);
the fetched pages confirmed some of them and showed only REST or v1 forms of
others.

What shapes this adapter:

- BatchRecognize reads audio only from Cloud Storage, so each run stages
  audio in `GOOGLE_STT_BUCKET` and deletes it afterwards (`delete_staged`).
- The Chirp 3 page caps BatchRecognize at about an hour, and at 20 minutes
  with word timestamps on; most of our recordings are longer. `chunk_s`
  (default 19 minutes) has the shared chunker cut them (`chunking.py`),
  speaker labels then being per piece; `speakers="pipeline"` avoids that.
- Chirp 3 is offered in the `us` and `eu` multi-regions; the recognizer is
  the implicit one, `projects/<project>/locations/<location>/recognizers/_`.
- Chirp 3's word confidence "isn't truly a confidence score" (vendor's
  words), so it is off by default and `score` stays None.
- Data logging is off unless the project opted in; there is no per-request
  flag. Check the project setting (spec §9).
- `model="medical_conversation"` reaches the registry's
  `google_medical_conversation` row; it does no phrase adaptation, so
  `phrases` is refused for it.
"""
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import CloudTranscriber, SpeakerLabels, segments_from_words, word


@dataclass
class GoogleParams:
    model: str = "chirp_3"
    location: str = "us"
    language_codes: tuple = ("en-US",)
    enable_word_time_offsets: bool = True
    enable_word_confidence: bool = False
    enable_automatic_punctuation: bool = False
    profanity_filter: bool = False
    diarize: bool = True
    min_speaker_count: int = 1       # the docs say both counts must be set;
    max_speaker_count: int = 6       # this wide range is "no hint" (spec §5)
    phrases: Optional[tuple] = None  # inline phrase set; chirp_3 takes up to 1000
    phrase_boost: Optional[float] = None
    denoise: bool = False
    chunk_s: Optional[float] = 1140  # pieces for the shared chunker; None sends the file whole
    chunk_overlap_s: float = 10.0
    dynamic_batching: bool = False   # cheaper, slower
    delete_staged: bool = True
    timeout_s: int = 7200


class GoogleTranscriber(CloudTranscriber):
    engine = "google"
    Params = GoogleParams
    env_keys = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_STT_BUCKET")
    packages = ("google-cloud-speech", "google-cloud-storage")

    def check_params(self):
        p = self.params
        if p.phrases and p.model == "medical_conversation":
            raise ValueError("medical_conversation does no phrase adaptation")
        if p.phrases and len(p.phrases) > 1000:
            raise ValueError("chirp_3 takes at most 1000 adaptation phrases")
        if p.min_speaker_count > p.max_speaker_count:
            raise ValueError("min_speaker_count > max_speaker_count")
        if p.chunk_s is not None and not 0 <= p.chunk_overlap_s < p.chunk_s:
            raise ValueError("need 0 <= chunk_overlap_s < chunk_s")

    def recognition_config(self):
        from google.cloud.speech_v2.types import cloud_speech as cs

        p = self.params
        features = cs.RecognitionFeatures(
            enable_word_time_offsets=p.enable_word_time_offsets,
            enable_word_confidence=p.enable_word_confidence,
            enable_automatic_punctuation=p.enable_automatic_punctuation,
            profanity_filter=p.profanity_filter,
        )
        if p.diarize:
            features.diarization_config = cs.SpeakerDiarizationConfig(
                min_speaker_count=p.min_speaker_count, max_speaker_count=p.max_speaker_count)
        config = cs.RecognitionConfig(
            auto_decoding_config=cs.AutoDetectDecodingConfig(),
            model=p.model, language_codes=list(p.language_codes), features=features)
        if p.phrases:
            phrase_set = cs.PhraseSet(phrases=[{"value": t} for t in p.phrases],
                                      boost=p.phrase_boost or 0)
            config.adaptation = cs.SpeechAdaptation(phrase_sets=[
                cs.SpeechAdaptation.AdaptationPhraseSet(inline_phrase_set=phrase_set)])
        if p.denoise:
            config.denoiser_config = cs.DenoiserConfig(denoise_audio=True)
        return config

    def call(self, audio_path, recording_id, camera):
        from google.api_core.client_options import ClientOptions
        from google.cloud import storage
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech as cs

        p = self.params
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        bucket = storage.Client(project=project).bucket(os.environ["GOOGLE_STT_BUCKET"])
        endpoint = "speech.googleapis.com" if p.location == "global" else f"{p.location}-speech.googleapis.com"
        client = SpeechClient(client_options=ClientOptions(api_endpoint=endpoint))
        recognizer = f"projects/{project}/locations/{p.location}/recognizers/_"

        blob = bucket.blob(f"multidata-stt/{recording_id}/{uuid.uuid4().hex[:8]}{Path(audio_path).suffix}")
        blob.upload_from_filename(str(audio_path))
        uri = f"gs://{bucket.name}/{blob.name}"
        try:
            request = cs.BatchRecognizeRequest(
                recognizer=recognizer, config=self.recognition_config(),
                files=[cs.BatchRecognizeFileMetadata(uri=uri)],
                recognition_output_config=cs.RecognitionOutputConfig(
                    inline_response_config=cs.InlineOutputConfig()))
            if p.dynamic_batching:
                request.processing_strategy = (
                    cs.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING)
            operation = client.batch_recognize(request=request)
            response = operation.result(timeout=p.timeout_s)
        finally:
            if p.delete_staged:
                blob.delete()
        file_result = response.results[uri]
        if file_result.error and file_result.error.code:
            raise RuntimeError(f"Google STT failed: {file_result.error.message}")
        transcript = (file_result.inline_result.transcript
                      if file_result.inline_result else file_result.transcript)
        native = json.loads(type(transcript).to_json(transcript))
        return native, {"request_id": operation.operation.name, "model_returned": None}

    def normalize(self, native):
        speakers = SpeakerLabels()
        words, language, seen = [], "en", set()
        for result in native.get("results", []):
            language = (_get(result, "languageCode", "language_code") or language).split("-")[0]
            for alt in result.get("alternatives", [])[:1]:
                for w in alt.get("words", []):
                    start = _offset(_get(w, "startOffset", "start_offset"))
                    end = _offset(_get(w, "endOffset", "end_offset"))
                    key = (w.get("word"), start, end)
                    if key in seen:   # diarized results may repeat earlier words
                        continue
                    seen.add(key)
                    words.append(word(
                        w.get("word", ""), start, end,
                        score=w.get("confidence") if self.params.enable_word_confidence else None,
                        speaker=speakers(_get(w, "speakerLabel", "speaker_label"))))
        words.sort(key=lambda w: (w["start"] is None, w["start"] or 0))
        return {"language": language, "segments": segments_from_words(words),
                "word_timing": self.params.enable_word_time_offsets}


def _get(d, camel, snake):
    return d.get(camel, d.get(snake))


def _offset(value):
    """A protobuf Duration as JSON ("1.100s") -> seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return float(value.rstrip("s"))
