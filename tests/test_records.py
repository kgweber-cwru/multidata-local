from multidata.records import SCHEMA_VERSION, finalize_record, validate_record

# The actual shape multidata.asr.transcribe_faster_whisper returns today
# (pre-diarization-merge: no schema_version/engine/word_timing/chunked/
# provenance yet, and words have no "speaker") -- Phase 1.5's "done when":
# existing faster_whisper output validates unchanged.
RAW_FASTER_WHISPER_SHAPE = {
    "language": "en",
    "segments": [
        {
            "start": 0.0,
            "end": 2.5,
            "text": " well, uh, hello",
            "words": [
                {"word": "well,", "start": 0.0, "end": 0.4, "score": 0.9},
                {"word": "uh,", "start": 0.4, "end": 0.6, "score": 0.8},
                {"word": "hello", "start": 0.6, "end": 1.0, "score": 0.95},
            ],
        }
    ],
}


class TestValidateRawEngineOutput:
    def test_raw_faster_whisper_shape_validates(self):
        assert validate_record(RAW_FASTER_WHISPER_SHAPE) == []

    def test_missing_segments_is_a_problem(self):
        problems = validate_record({"language": "en"})
        assert any("segments" in p for p in problems)

    def test_not_a_dict_is_a_problem(self):
        assert validate_record(["not", "a", "dict"]) == ["record is not a dict"]

    def test_segment_missing_required_field(self):
        record = {"language": "en", "segments": [{"start": 0.0, "text": "hi"}]}
        problems = validate_record(record)
        assert any("end" in p for p in problems)

    def test_word_missing_required_field(self):
        record = {
            "language": "en",
            "segments": [{
                "start": 0.0, "end": 1.0, "text": "hi",
                "words": [{"word": "hi", "start": 0.0}],
            }],
        }
        problems = validate_record(record)
        assert any("end" in p for p in problems)


class TestWordTimingFlag:
    def test_word_timing_false_allows_segment_without_words(self):
        record = {
            "language": "en",
            "word_timing": False,
            "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
        }
        assert validate_record(record) == []

    def test_word_timing_true_requires_words_key(self):
        record = {
            "language": "en",
            "word_timing": True,
            "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
        }
        problems = validate_record(record)
        assert any("words" in p for p in problems)

    def test_word_timing_defaults_to_true_when_absent(self):
        record = {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}
        problems = validate_record(record)
        assert any("words" in p for p in problems)


class TestTypeChecking:
    def test_wrong_type_for_chunked_is_a_problem(self):
        record = dict(RAW_FASTER_WHISPER_SHAPE, chunked="no")
        problems = validate_record(record)
        assert any("chunked" in p for p in problems)

    def test_speaker_must_be_a_string_when_present(self):
        record = {
            "language": "en",
            "segments": [{
                "start": 0.0, "end": 1.0, "text": "hi",
                "words": [{"word": "hi", "start": 0.0, "end": 1.0, "speaker": 0}],
            }],
        }
        problems = validate_record(record)
        assert any("speaker" in p for p in problems)


class TestFinalizeRecord:
    def test_stamps_defaults(self):
        record = finalize_record(RAW_FASTER_WHISPER_SHAPE, engine="faster_whisper")
        assert record["schema_version"] == SCHEMA_VERSION
        assert record["engine"] == "faster_whisper"
        assert record["word_timing"] is True
        assert record["chunked"] is False
        assert record["provenance"] == {}

    def test_finalized_record_validates(self):
        record = finalize_record(RAW_FASTER_WHISPER_SHAPE, engine="faster_whisper")
        assert validate_record(record) == []

    def test_does_not_mutate_input(self):
        original = dict(RAW_FASTER_WHISPER_SHAPE)
        finalize_record(RAW_FASTER_WHISPER_SHAPE, engine="faster_whisper")
        assert RAW_FASTER_WHISPER_SHAPE == original

    def test_does_not_override_existing_engine(self):
        record = dict(RAW_FASTER_WHISPER_SHAPE, engine="already_set")
        finalized = finalize_record(record, engine="faster_whisper")
        assert finalized["engine"] == "already_set"

    def test_word_timing_false_is_honored(self):
        record = finalize_record({"language": "en", "segments": []},
                                  engine="google", word_timing=False)
        assert record["word_timing"] is False
