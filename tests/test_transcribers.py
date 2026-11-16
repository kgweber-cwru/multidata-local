"""multidata.transcribers: parameters, the cloud interlock, and each vendor's
normalizer on hand-written responses shaped like the documented ones. No
network, no model, no real manifest: the manifest is a temporary file and the
engines are stubbed."""
import json

import pytest

from multidata import asr, manifest, records, transcribers
from multidata.models import Recording
from multidata.transcribers import CloudReleaseError
from multidata.transcribers.assemblyai import AssemblyAITranscriber
from multidata.transcribers.base import SpeakerLabels, segments_from_words, word
from multidata.transcribers.elevenlabs import ElevenLabsTranscriber
from multidata.transcribers.google import GoogleTranscriber, _offset


@pytest.fixture
def db(tmp_path):
    """A manifest with one recording (not cloud-released) and its audio."""
    path = tmp_path / "m.sqlite"
    manifest.add_recording(Recording(recording_id="999"), path=path)
    wav = tmp_path / "999.wav"
    wav.write_bytes(b"RIFF")
    manifest.add_video("999", "16", path=path, audio_path=str(wav))
    return path


def _release(db):
    manifest.update_recording("999", path=db, cloud_release=1,
                              cloud_release_basis="simulated_patient")


class TestRegistry:
    def test_every_engine_is_local_whisper_or_a_cloud_vendor(self):
        assert set(transcribers.engines()) == set(asr.ENGINES) | {"elevenlabs", "assemblyai", "google"}

    def test_cloud_engines_have_registry_rows(self):
        from multidata.paths import ROOT
        from multidata.providers import load_providers

        rows = load_providers(ROOT / "benchmarks" / "configs" / "providers.yaml")
        assert set(transcribers.CLOUD) <= set(rows)

    def test_unknown_engine(self):
        with pytest.raises(ValueError, match="unknown engine"):
            transcribers.get_transcriber("nope")

    @pytest.mark.parametrize("engine", ["elevenlabs", "assemblyai", "google", "whisperx_disfluent"])
    def test_unknown_parameter_is_refused_by_name(self, engine):
        with pytest.raises(TypeError, match="no_such_param"):
            transcribers.get_transcriber(engine, no_such_param=1)

    def test_params_lists_defaults(self):
        p = transcribers.params("assemblyai")
        assert p["model"] == "universal-3-5-pro" and p["disfluencies"] is True
        assert transcribers.params("whisperx_disfluent")["condition_on_previous_text"] is True


class TestParamChecks:
    def test_assemblyai_prompt_and_keyterms_exclusive(self):
        with pytest.raises(ValueError, match="prompt"):
            AssemblyAITranscriber(prompt="verbatim", keyterms_prompt=("dyspnea",))

    def test_assemblyai_speaker_count_forms_exclusive(self):
        with pytest.raises(ValueError):
            AssemblyAITranscriber(speakers_expected=3, max_speakers_expected=4)

    def test_assemblyai_body(self):
        t = AssemblyAITranscriber(min_speakers_expected=2, max_speakers_expected=4,
                                  domain="medical-v1")
        body = t.request_body("https://cdn/x")
        assert body["speech_models"] == ["universal-3-5-pro"]
        assert body["speaker_options"] == {"min_speakers_expected": 2, "max_speakers_expected": 4}
        assert body["domain"] == "medical-v1" and "prompt" not in body

    def test_elevenlabs_keyterm_rules(self):
        with pytest.raises(ValueError, match="keyterm"):
            ElevenLabsTranscriber(keyterms=("[bad]",))
        with pytest.raises(ValueError, match="scribe_v3"):
            ElevenLabsTranscriber(model="scribe_v2", numbers_format="words")

    def test_google_medical_takes_no_phrases(self):
        with pytest.raises(ValueError, match="adaptation"):
            GoogleTranscriber(model="medical_conversation", phrases=("dyspnea",))


class TestInterlock:
    @pytest.mark.parametrize("engine", sorted(transcribers.CLOUD))
    def test_refuses_before_reading_audio(self, db, monkeypatch, engine):
        t = transcribers.get_transcriber(engine)
        touched = []
        monkeypatch.setattr(t, "call", lambda *a: touched.append(a))
        monkeypatch.setattr("multidata.transcribers.base._audio_path",
                            lambda *a: touched.append(a))
        with pytest.raises(CloudReleaseError):
            t.transcribe("999", manifest_path=db)
        assert touched == []

    def test_released_recording_proceeds(self, db, monkeypatch):
        _release(db)
        monkeypatch.setenv("ASSEMBLYAI_API_KEY", "x")
        t = AssemblyAITranscriber()
        monkeypatch.setattr(t, "call", lambda *a: (ASSEMBLYAI, {"request_id": "abc",
                                                                "model_returned": "universal-3-5-pro"}))
        out = t.transcribe("999", manifest_path=db)
        prov = out.record["provenance"]
        assert prov["cloud_release_basis"] == "simulated_patient"
        assert prov["request_id"] == "abc" and prov["params"]["disfluencies"] is True
        assert out.native is ASSEMBLYAI

    def test_missing_key_is_named(self, db, monkeypatch):
        _release(db)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.setattr("multidata.transcribers.base.load_env", lambda: None)
        with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
            ElevenLabsTranscriber().transcribe("999", manifest_path=db)

    def test_unknown_recording(self, db):
        with pytest.raises(KeyError):
            AssemblyAITranscriber().transcribe("000", manifest_path=db)


class TestLocal:
    def test_whisper_runs_through_asr_transcribe_with_pipeline_rttm(self, db, monkeypatch):
        seen = {}

        def fake_transcribe(audio_path, engine, diarize_rttm, **kwargs):
            seen.update(engine=engine, rttm=diarize_rttm, **kwargs)
            return {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": " uh 42",
                    "words": [{"word": "uh", "start": 0.0, "end": 0.4, "score": 0.9,
                               "speaker": "SPEAKER_00"}, {"word": "42"}]}]}

        monkeypatch.setattr(asr, "transcribe", fake_transcribe)
        monkeypatch.setattr("multidata.transcribers.whisper.DATA_DIR", db.parent / "data")
        out = transcribers.transcribe_recording("999", "whisperx_disfluent", manifest_path=db,
                                                model_name="medium")
        assert seen["engine"] == "whisperx_disfluent" and seen["model_name"] == "medium"
        assert str(seen["rttm"]).endswith("diarization/999/16.rttm")
        assert out.model == "medium" and out.params["condition_on_previous_text"] is True
        unaligned = out.record["segments"][0]["words"][1]
        assert unaligned["start"] is None and unaligned["end"] is None
        assert records.validate_record(out.record) == []
        assert out.record["provenance"]["kind"] == "local"


ELEVENLABS = {
    "language_code": "eng", "language_probability": 0.98, "text": "Hi um (laughter) okay",
    "transcription_id": "tr_1", "audio_duration_secs": 3.0,
    "words": [
        {"text": "Hi", "type": "word", "start": 0.0, "end": 0.3, "logprob": 0.0, "speaker_id": "speaker_1"},
        {"text": " ", "type": "spacing", "start": 0.3, "end": 0.4, "logprob": 0.0, "speaker_id": "speaker_1"},
        {"text": "um", "type": "word", "start": 0.4, "end": 0.6, "logprob": -0.5, "speaker_id": "speaker_1"},
        {"text": "(laughter)", "type": "audio_event", "start": 0.7, "end": 1.2, "logprob": 0.0,
         "speaker_id": "speaker_0"},
        {"text": "okay", "type": "word", "start": 1.3, "end": 1.6, "logprob": -0.1, "speaker_id": "speaker_0"},
    ],
}

ASSEMBLYAI = {
    "id": "abc", "status": "completed", "language_code": "en_us", "speech_model_used": "universal-3-5-pro",
    "audio_duration": 3,
    "words": [{"text": "So", "start": 0, "end": 250, "confidence": 0.9, "speaker": "A"},
              {"text": "mm-hmm", "start": 900, "end": 1300, "confidence": 0.8, "speaker": "B"}],
    "utterances": [
        {"text": "So", "start": 0, "end": 250, "confidence": 0.9, "speaker": "A",
         "words": [{"text": "So", "start": 0, "end": 250, "confidence": 0.9, "speaker": "A"}]},
        {"text": "mm-hmm", "start": 900, "end": 1300, "confidence": 0.8, "speaker": "B",
         "words": [{"text": "mm-hmm", "start": 900, "end": 1300, "confidence": 0.8, "speaker": "B"}]},
    ],
}

GOOGLE = {"recognizer": "projects/p/locations/us/recognizers/_", "chunks": [
    {"index": 0, "offset_s": 0.0, "duration_s": 1140.0, "uri": "gs://b/00.flac", "operation": "op0",
     "response": {"results": [{"languageCode": "en-us", "alternatives": [{"transcript": "chest pain", "words": [
         {"word": "chest", "startOffset": "1.100s", "endOffset": "1.500s", "speakerLabel": "1"},
         {"word": "pain", "startOffset": "1.500s", "endOffset": "2s", "speakerLabel": "1"}]}]}]}},
    {"index": 1, "offset_s": 1140.0, "duration_s": 60.0, "uri": "gs://b/01.flac", "operation": "op1",
     "response": {"results": [{"languageCode": "en-us", "alternatives": [{"transcript": "okay", "words": [
         {"word": "okay", "startOffset": "0.5s", "endOffset": "0.9s", "speakerLabel": "1",
          "confidence": 0.5}]}]}]}},
]}


class TestNormalizers:
    def test_elevenlabs(self):
        rec = ElevenLabsTranscriber().normalize(ELEVENLABS)
        words = [w for s in rec["segments"] for w in s["words"]]
        assert [w["word"] for w in words] == ["Hi", "um", "[laughter]", "okay"]
        assert words[0]["speaker"] == "SPEAKER_00" and words[2]["speaker"] == "SPEAKER_01"
        assert words[0]["score"] == 1.0 and 0 < words[1]["score"] < 1
        assert rec["language"] == "en" and rec["word_timing"] is True

    def test_assemblyai_uses_utterances_and_seconds(self):
        rec = AssemblyAITranscriber().normalize(ASSEMBLYAI)
        assert [s["speaker"] for s in rec["segments"]] == ["SPEAKER_00", "SPEAKER_01"]
        assert rec["segments"][1]["words"][0]["start"] == 0.9
        assert rec["language"] == "en"

    def test_assemblyai_without_utterances_groups_words(self):
        rec = AssemblyAITranscriber().normalize({**ASSEMBLYAI, "utterances": None})
        assert len(rec["segments"]) == 2

    def test_google_offsets_chunks_and_unlinked_speakers(self):
        rec = GoogleTranscriber().normalize(GOOGLE)
        words = [w for s in rec["segments"] for w in s["words"]]
        assert [(w["word"], w["start"]) for w in words] == [("chest", 1.1), ("pain", 1.5), ("okay", 1140.5)]
        # speaker "1" in piece 0 and in piece 1 are not assumed to be the same person
        assert words[0]["speaker"] != words[2]["speaker"]
        assert all(w["score"] is None for w in words)   # chirp_3 confidence isn't one
        assert rec["language"] == "en"

    def test_google_drops_repeated_words(self):
        doubled = json.loads(json.dumps(GOOGLE))
        results = doubled["chunks"][0]["response"]["results"]
        results.append(json.loads(json.dumps(results[0])))
        rec = GoogleTranscriber().normalize(doubled)
        assert sum(len(s["words"]) for s in rec["segments"]) == 3

    def test_google_duration_strings(self):
        assert _offset("2s") == 2.0 and _offset("1.100s") == 1.1 and _offset(None) is None

    @pytest.mark.parametrize("t, native", [(ElevenLabsTranscriber(), ELEVENLABS),
                                           (AssemblyAITranscriber(), ASSEMBLYAI),
                                           (GoogleTranscriber(), GOOGLE)])
    def test_normalized_records_validate(self, t, native):
        rec = records.finalize_record(t.normalize(native), t.engine)
        assert records.validate_record(rec) == []


class TestHelpers:
    def test_speaker_labels_in_order_of_appearance(self):
        labels = SpeakerLabels()
        assert [labels(x) for x in ("B", "A", "B", None)] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00", None]

    def test_segments_break_on_speaker_and_gap(self):
        ws = [word("a", 0, 1, speaker="S0"), word("b", 1.2, 2, speaker="S0"),
              word("c", 5, 6, speaker="S0"), word("d", 6, 7, speaker="S1")]
        assert [s["text"] for s in segments_from_words(ws)] == ["a b", "c", "d"]


class TestSave:
    def test_writes_both_views_and_refuses_overwrite(self, tmp_path):
        t = transcribers.Transcription("999", "16", "assemblyai", "universal-3-5-pro", {}, ASSEMBLYAI,
                                       {"language": "en", "segments": []})
        m = tmp_path / "m.sqlite"
        normalized, raw = t.save(tmp_path, manifest_path=m)
        assert normalized.name == "16_assemblyai_universal-3-5-pro.json"
        assert json.loads(raw.read_text())["id"] == "abc"
        with pytest.raises(FileExistsError):
            t.save(tmp_path, manifest_path=m)
        t.save(tmp_path, overwrite=True, manifest_path=m)
        assert len(manifest.all_transcripts(m)) == 1   # overwrite replaced the row

    def test_registers_the_run_in_the_manifest(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(asr, "transcribe", lambda *a, **k: {"language": "en", "segments": []})
        monkeypatch.setattr("multidata.transcribers.whisper.DATA_DIR", tmp_path / "data")
        out = transcribers.transcribe_recording("999", "faster_whisper", manifest_path=db,
                                                model_name="tiny")
        out.save(tmp_path / "out", manifest_path=db)
        [row] = manifest.all_transcripts(db, recording_id="999")
        assert (row["engine"], row["model"], row["kind"]) == ("faster_whisper", "tiny", "local")
        assert row["params"]["model_name"] == "tiny"
        assert row["params_hash"] == manifest.params_hash(out.params)
        assert row["normalized_path"].endswith("16_faster_whisper_tiny.json")
