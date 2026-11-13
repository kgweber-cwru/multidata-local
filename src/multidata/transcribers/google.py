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
  with word timestamps on. Our recordings run 10-60 minutes, so audio is cut
  into `chunk_s` pieces (default 19 minutes), sent one request per piece,
  and the times are stitched back. The record says `chunked: true`, and
  `native` keeps each piece's offset (plan 7.4).
- **Speaker labels don't carry across pieces.** Each piece is diarized on
  its own, so "speaker 1" in piece 0 and "speaker 1" in piece 1 may be
  different people. They get separate `SPEAKER_NN` labels here rather than
  a false match. For speaker-level comparison on a chunked recording, use
  the pyannote control arm (spec §5, plan 4.5).
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
import subprocess
import tempfile
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
    chunk_s: Optional[float] = 1140  # None sends the file whole
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
        if p.chunk_s is not None and p.chunk_s <= 0:
            raise ValueError("chunk_s must be positive, or None")

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
        config = self.recognition_config()
        prefix = f"multidata-stt/{recording_id}/{uuid.uuid4().hex[:8]}"

        chunks, staged = [], []
        with tempfile.TemporaryDirectory() as tmp:
            pieces = _cut(audio_path, Path(tmp), p.chunk_s)
            try:
                for index, (piece, offset_s, duration_s) in enumerate(pieces):
                    blob = bucket.blob(f"{prefix}/{index:02d}.flac")
                    blob.upload_from_filename(str(piece))
                    staged.append(blob)
                    uri = f"gs://{bucket.name}/{blob.name}"
                    request = cs.BatchRecognizeRequest(
                        recognizer=recognizer, config=config,
                        files=[cs.BatchRecognizeFileMetadata(uri=uri)],
                        recognition_output_config=cs.RecognitionOutputConfig(
                            inline_response_config=cs.InlineOutputConfig()))
                    if p.dynamic_batching:
                        request.processing_strategy = (
                            cs.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING)
                    operation = client.batch_recognize(request=request)
                    response = operation.result(timeout=p.timeout_s)
                    file_result = response.results[uri]
                    if file_result.error and file_result.error.code:
                        raise RuntimeError(f"Google STT failed on piece {index}: "
                                           f"{file_result.error.message}")
                    transcript = (file_result.inline_result.transcript
                                  if file_result.inline_result else file_result.transcript)
                    chunks.append({
                        "index": index, "offset_s": offset_s, "duration_s": duration_s,
                        "uri": uri, "operation": operation.operation.name,
                        "response": json.loads(type(transcript).to_json(transcript)),
                    })
            finally:
                if p.delete_staged:
                    for blob in staged:
                        blob.delete()

        native = {"recognizer": recognizer, "chunks": chunks}
        return native, {
            "request_id": ";".join(c["operation"] for c in chunks),
            "model_returned": None,
            "audio_duration_s": round(sum(c["duration_s"] for c in chunks), 3),
            "chunked": len(chunks) > 1,
            "notes": (f"{len(chunks)} pieces of up to {p.chunk_s} s; speaker labels are "
                      "per piece" if len(chunks) > 1 else None),
        }

    def normalize(self, native):
        speakers = SpeakerLabels()
        words, language = [], "en"
        for chunk in native["chunks"]:
            offset, seen = chunk["offset_s"], set()
            for result in chunk["response"].get("results", []):
                language = (_get(result, "languageCode", "language_code") or language).split("-")[0]
                for alt in result.get("alternatives", [])[:1]:
                    for w in alt.get("words", []):
                        start = _offset(_get(w, "startOffset", "start_offset"))
                        end = _offset(_get(w, "endOffset", "end_offset"))
                        key = (w.get("word"), start, end)
                        if key in seen:   # diarized results may repeat earlier words
                            continue
                        seen.add(key)
                        label = _get(w, "speakerLabel", "speaker_label")
                        words.append(word(
                            w.get("word", ""),
                            None if start is None else round(start + offset, 3),
                            None if end is None else round(end + offset, 3),
                            score=w.get("confidence") if self.params.enable_word_confidence else None,
                            speaker=speakers(f"{chunk['index']}:{label}") if label else None))
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


def _cut(audio_path, out_dir, chunk_s):
    """Mono 16 kHz FLAC pieces of at most `chunk_s` seconds:
    [(path, offset_s, duration_s)]."""
    import soundfile

    total = soundfile.info(str(audio_path)).duration
    step = chunk_s or total
    pieces, offset, index = [], 0.0, 0
    while offset < total:
        length = min(step, total - offset)
        piece = out_dir / f"{index:02d}.flac"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{offset:.3f}",
                        "-t", f"{length:.3f}", "-i", str(audio_path), "-ac", "1", "-ar", "16000",
                        str(piece)], check=True)
        pieces.append((piece, round(offset, 3), round(length, 3)))
        offset += step
        index += 1
    return pieces
