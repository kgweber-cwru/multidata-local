"""multidata.transcribers: parameters, the cloud interlock, and each vendor's
normalizer on hand-written responses shaped like the documented ones. No
network, no model, no real manifest: the manifest is a temporary file and the
engines are stubbed."""
import json

import pytest

from multidata import asr, manifest, records, transcribers
from multidata.models import Recording
from multidata.transcribers import CloudReleaseError, chunking
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
    def test_excerpt_with_pipeline_speakers_is_refused(self, db, tmp_path):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"RIFF")
        with pytest.raises(ValueError, match="excerpt"):
            transcribers.transcribe_recording("999", "faster_whisper", manifest_path=db,
                                              audio_path=clip)

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

GOOGLE = {"results": [{"languageCode": "en-us", "alternatives": [{"transcript": "chest pain okay", "words": [
    {"word": "chest", "startOffset": "1.100s", "endOffset": "1.500s", "speakerLabel": "1"},
    {"word": "pain", "startOffset": "1.500s", "endOffset": "2s", "speakerLabel": "1"},
    {"word": "okay", "startOffset": "2.5s", "endOffset": "2.9s", "speakerLabel": "2",
     "confidence": 0.5}]}]}]}


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

    def test_google(self):
        rec = GoogleTranscriber().normalize(GOOGLE)
        words = [w for s in rec["segments"] for w in s["words"]]
        assert [(w["word"], w["start"]) for w in words] == [("chest", 1.1), ("pain", 1.5), ("okay", 2.5)]
        assert [w["speaker"] for w in words] == ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01"]
        assert all(w["score"] is None for w in words)   # chirp_3 confidence isn't one
        assert rec["language"] == "en"

    def test_google_drops_repeated_words(self):
        doubled = json.loads(json.dumps(GOOGLE))
        results = doubled["results"]
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


def _tone(path, seconds, sr=16000):
    import numpy as np
    import soundfile

    soundfile.write(str(path), np.zeros(int(seconds * sr), dtype="float32"), sr)
    return path


def _words(*items):
    """(word, start, end, speaker) -> one normalized result."""
    ws = [word(t, s, e, speaker=spk) for t, s, e, spk in items]
    return {"language": "en", "segments": segments_from_words(ws)}


class TestChunking:
    def test_plan_keeps_pieces_under_the_cap_and_overlapping(self):
        turns = [(t, t + 5, "AB"[t // 5 % 2]) for t in range(0, 3000, 5)]
        spans = chunking.plan(turns, 3001, chunk_s=1140, overlap_s=60)
        assert all(e - s <= 1140 for s, e in spans)
        assert all(a[1] - b[0] == 60 for a, b in zip(spans, spans[1:]))
        assert spans[0][0] == 0 and spans[-1][1] == 3001

    def test_plan_puts_the_overlap_where_turns_change(self):
        # A monologue (exam instructions) except for a lively stretch at 900-960 s
        turns = [(t, t + 5, "A") for t in range(0, 2000, 5)]
        turns += [(t + 2, t + 4, "B") for t in range(900, 960, 5)]
        spans = chunking.plan(turns, 2001, chunk_s=1140, overlap_s=60)
        assert spans[1][0] == 900

    def test_short_audio_is_one_piece(self):
        assert chunking.plan([], 100, 1140, 60) == [(0.0, 100.0)]

    def test_cut_writes_each_span(self, tmp_path):
        pieces = chunking.cut(_tone(tmp_path / "a.wav", 25), tmp_path, [(0, 10), (8, 18)])
        assert [(o, d) for _, o, d in pieces] == [(0, 10), (8, 10)]
        assert all(p.exists() for p, _, _ in pieces)

    def test_merge_takes_each_overlap_word_once(self):
        # piece 0 covers 0-10 s, piece 1 covers 8-18 s; overlap 8-10, midpoint 9
        a = _words(("one", 1, 2, "SPEAKER_00"), ("eight", 8.5, 8.8, "SPEAKER_00"),
                   ("nine", 9.2, 9.6, "SPEAKER_00"))
        b = _words(("eight", 0.5, 0.8, "SPEAKER_00"), ("nine", 1.2, 1.6, "SPEAKER_00"),
                   ("twelve", 4, 4.5, "SPEAKER_00"))
        merged, _ = chunking.merge([(0.0, 10.0, a), (8.0, 10.0, b)])
        words = [w for s in merged["segments"] for w in s["words"]]
        assert [(w["word"], w["start"]) for w in words] == [
            ("one", 1), ("eight", 8.5), ("nine", 9.2), ("twelve", 12.0)]

    def test_merge_takes_an_overlap_from_the_piece_that_heard_it(self):
        # Overlap 50-60 s: piece 0 heard one word there, piece 1 heard eight.
        a = _words(("early", 10, 11, "SPEAKER_00"), ("tail", 51, 51.5, "SPEAKER_00"))
        b = _words(*[(f"w{k}", k, k + 0.5, "SPEAKER_00") for k in range(1, 9)])
        merged, notes = chunking.merge([(0.0, 60.0, a), (50.0, 40.0, b)])
        words = [w["word"] for s in merged["segments"] for w in s["words"]]
        assert words == ["early"] + [f"w{k}" for k in range(1, 9)]
        assert "piece 1: overlap taken from this piece (8 words against 1)" in notes

    def test_merge_links_speakers_through_the_overlap(self):
        # Piece 0 calls the student SPEAKER_00 and the patient SPEAKER_01; the
        # vendor numbers them the other way round in piece 1.
        overlap = [(f"w{k}", 50 + k, 50.5 + k, k % 2) for k in range(12)]
        a = _words(*[(t, s, e, f"SPEAKER_0{x}") for t, s, e, x in overlap])
        b = _words(*[(t, s - 50, e - 50, f"SPEAKER_0{1 - x}") for t, s, e, x in overlap],
                   ("later", 30, 31, "SPEAKER_00"))
        merged, notes = chunking.merge([(0.0, 62.0, a), (50.0, 40.0, b)])
        words = {w["word"]: w["speaker"] for s in merged["segments"] for w in s["words"]}
        assert words["w1"] == words["later"] and words["w0"] != words["w1"]
        assert notes == []

    def test_a_silent_speaker_stays_unlinked_and_is_noted(self):
        # The patient (SPEAKER_01 in piece 1) says nothing in the overlap.
        overlap = [(f"w{k}", 50 + k, 50.5 + k, "SPEAKER_00") for k in range(8)]
        a = _words(*overlap)
        b = _words(*[(t, s - 50, e - 50, x) for t, s, e, x in overlap],
                   ("okay", 30, 31, "SPEAKER_01"))
        merged, notes = chunking.merge([(0.0, 60.0, a), (50.0, 40.0, b)])
        speakers = {w["speaker"] for s in merged["segments"] for w in s["words"]}
        assert len(speakers) == 2 and notes == ["piece 1: 1 speaker label(s) not linked to the previous piece"]

    def test_turns_link_a_speaker_the_overlap_cannot(self):
        # The overlap (50-60 s) is silent; the diarize stage hears the student
        # as "S" in both pieces and the patient as "P" only in piece 0.
        a = _words(*[(f"a{k}", k, k + 0.8, "SPEAKER_00") for k in range(0, 20, 2)],
                   *[(f"p{k}", k, k + 0.8, "SPEAKER_01") for k in range(21, 40, 2)])
        b = _words(*[(f"b{k}", k, k + 0.8, "SPEAKER_00") for k in range(20, 30)],
                   *[(f"n{k}", k, k + 0.8, "SPEAKER_01") for k in range(30, 40)])
        turns = [(0, 20, "S"), (20, 40, "P"), (70, 80, "S"), (80, 90, "X")]
        merged, notes = chunking.merge([(0.0, 60.0, a), (50.0, 40.0, b)], turns)
        words = {w["word"]: w["speaker"] for s in merged["segments"] for w in s["words"]}
        assert words["b20"] == words["a0"]
        assert words["n30"] not in (words["a0"], words["p21"])
        assert notes == ["piece 1: 1 speaker label(s) linked through the diarize stage's turns",
                         "piece 1: 1 speaker label(s) not linked to the previous piece"]

    def test_plan_ignores_flicker_turns(self):
        turns = [(t, t + 5, "A") for t in range(0, 2000, 5)]
        turns += [(1000 + k * 0.1, 1000 + k * 0.1 + 0.05, "AB"[k % 2]) for k in range(40)]
        turns += [(t + 2, t + 4, "B") for t in range(700, 760, 5)]
        assert chunking.plan(turns, 2001, chunk_s=1140, overlap_s=60)[1][0] == 700

    def test_no_linking_without_an_overlap(self):
        a = _words(("hi", 1, 2, "SPEAKER_00"))
        b = _words(("hi", 1, 2, "SPEAKER_00"))
        merged, notes = chunking.merge([(0.0, 10.0, a), (100.0, 10.0, b)])
        assert len({w["speaker"] for s in merged["segments"] for w in s["words"]}) == 2
        assert notes == []

    def test_assign_speakers_by_most_overlap(self):
        result = _words(("hi", 0, 1, "SPEAKER_00"), ("there", 1.2, 2, "SPEAKER_00"),
                        ("um", 5, 5, None), ("gap", 20, 21, "SPEAKER_00"))
        turns = [(0, 1.1, "SPEAKER_01"), (1.1, 6, "SPEAKER_00")]
        words = [w for s in chunking.assign_speakers(result, turns)["segments"] for w in s["words"]]
        assert [w.get("speaker") for w in words] == ["SPEAKER_01", "SPEAKER_00", "SPEAKER_00", None]

    def test_long_audio_is_sent_in_planned_pieces(self, db, monkeypatch):
        _release(db)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        monkeypatch.setenv("GOOGLE_STT_BUCKET", "b")
        monkeypatch.setattr("multidata.transcribers.chunking.DATA_DIR", db.parent / "data")
        _tone(manifest.get_video("999", "16", db).audio_path, 400)
        rttm = chunking.pipeline_rttm("999", "16")
        rttm.parent.mkdir(parents=True)
        rttm.write_text("".join(f"SPEAKER 16 1 {s} 5.0 <NA> <NA> SPEAKER_0{s // 5 % 2} <NA> <NA>\n"
                                for s in range(0, 400, 5)))
        t = GoogleTranscriber(chunk_s=200, chunk_overlap_s=20)
        sent = []

        def fake_call(audio, recording_id, camera):
            sent.append(audio.name)
            return GOOGLE, {"request_id": f"op{len(sent)}"}

        monkeypatch.setattr(t, "call", fake_call)
        out = t.transcribe("999", manifest_path=db)
        offsets = [c["offset_s"] for c in out.native["chunks"]]
        assert offsets == [0.0, 180.0, 360.0] and len(sent) == 3
        prov = out.record["provenance"]
        assert prov["request_id"] == "op1;op2;op3" and "3 pieces" in prov["notes"]
        assert out.record["chunked"] is True and prov["audio_duration_s"] == 400.0

    def test_long_excerpts_are_refused(self, db, tmp_path, monkeypatch):
        _release(db)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        monkeypatch.setenv("GOOGLE_STT_BUCKET", "b")
        clip = _tone(tmp_path / "clip.wav", 30)
        with pytest.raises(ValueError, match="excerpt"):
            GoogleTranscriber(chunk_s=20, chunk_overlap_s=5).transcribe(
                "999", manifest_path=db, audio_path=clip)

    def test_pipeline_speakers_replace_the_vendors(self, db, monkeypatch):
        _release(db)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
        monkeypatch.setattr("multidata.transcribers.chunking.DATA_DIR", db.parent / "data")
        rttm = chunking.pipeline_rttm("999", "16")
        rttm.parent.mkdir(parents=True)
        rttm.write_text("SPEAKER 16 1 0.0 5.0 <NA> <NA> SPEAKER_07 <NA> <NA>\n")
        t = ElevenLabsTranscriber(speakers="pipeline")
        monkeypatch.setattr(t, "call", lambda *a: (ELEVENLABS, {}))
        out = t.transcribe("999", manifest_path=db)
        words = [w for s in out.record["segments"] for w in s["words"]]
        assert {w["speaker"] for w in words} == {"SPEAKER_07"}
        assert out.params["speakers"] == "pipeline"

    def test_pipeline_speakers_need_the_rttm(self, db, monkeypatch):
        _release(db)
        monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
        monkeypatch.setattr("multidata.transcribers.chunking.DATA_DIR", db.parent / "data")
        t = ElevenLabsTranscriber(speakers="pipeline")
        monkeypatch.setattr(t, "call", lambda *a: (ELEVENLABS, {}))
        with pytest.raises(FileNotFoundError, match="diarize stages"):
            t.transcribe("999", manifest_path=db)

    def test_unknown_speaker_source(self):
        with pytest.raises(ValueError, match="speakers"):
            AssemblyAITranscriber(speakers="vendor")
