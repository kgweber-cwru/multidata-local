"""Dispatch-logic tests for multidata.asr.transcribe() -- which engine gets
called with which kwargs, and whether diarization gets merged in. All heavy
imports in asr.py (whisperx, faster_whisper, torch) are deferred inside
functions, so importing multidata.asr and monkeypatching asr.ENGINES entries
needs none of them installed. Keep new asr.py code inside that lazy-import
pattern or this stops being possible (see tests/README.md)."""
import pytest

from multidata import asr


def _stub_result(with_speaker=True):
    word = {"word": "hi", "start": 0.0, "end": 0.5}
    if with_speaker:
        word["speaker"] = "SPEAKER_00"
    return {"segments": [{"start": 0.0, "end": 0.5, "text": "hi", "words": [word]}]}


class TestRegistryConsistency:
    def test_takes_diarize_device_is_subset_of_diarizes_internally(self):
        assert asr.TAKES_DIARIZE_DEVICE <= asr.DIARIZES_INTERNALLY

    def test_diarizes_internally_is_subset_of_engines(self):
        assert asr.DIARIZES_INTERNALLY <= set(asr.ENGINES)

    def test_whisperx_disfluent_is_registered(self):
        assert "whisperx_disfluent" in asr.ENGINES
        assert "whisperx_disfluent" in asr.DIARIZES_INTERNALLY
        assert "whisperx_disfluent" in asr.TAKES_DIARIZE_DEVICE


class TestUnknownEngine:
    def test_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            asr.transcribe("fake.wav", engine="not_a_real_engine")


class TestDiarizeDeviceForwarding:
    @pytest.mark.parametrize("engine", sorted(asr.TAKES_DIARIZE_DEVICE))
    def test_diarize_device_forwarded_for_whisperx_family(self, monkeypatch, engine):
        received = {}

        def fake(audio_path, **kwargs):
            received.update(kwargs)
            return _stub_result()

        monkeypatch.setitem(asr.ENGINES, engine, fake)
        asr.transcribe("fake.wav", engine=engine, diarize_device="mps")
        assert received.get("diarize_device") == "mps"


class TestDiarizationMergeSkippedForInternalDiarizers:
    @pytest.mark.parametrize("engine", sorted(asr.DIARIZES_INTERNALLY))
    def test_merge_not_called(self, monkeypatch, engine):
        monkeypatch.setitem(asr.ENGINES, engine, lambda audio_path, **kw: _stub_result())

        def boom(*args, **kwargs):
            raise AssertionError(
                f"_diarize_and_merge must not be called for {engine!r}, "
                "which diarizes internally"
            )

        monkeypatch.setattr(asr, "_diarize_and_merge", boom)
        result = asr.transcribe("fake.wav", engine=engine)
        assert result["engine"] == engine


class TestDiarizationMergeCalledForExternalEngines:
    def test_merge_called_for_faster_whisper(self, monkeypatch):
        monkeypatch.setitem(
            asr.ENGINES, "faster_whisper",
            lambda audio_path, **kw: _stub_result(with_speaker=False),
        )
        called = {}

        def fake_merge(result, audio_path, rttm_path, device, num_speakers,
                        min_speakers, max_speakers):
            called["yes"] = True
            return result

        monkeypatch.setattr(asr, "_diarize_and_merge", fake_merge)
        asr.transcribe("fake.wav", engine="faster_whisper")
        assert called.get("yes") is True


class TestEngineFieldStamped:
    def test_engine_set_when_missing(self, monkeypatch):
        monkeypatch.setitem(
            asr.ENGINES, "whisperx_disfluent",
            lambda audio_path, **kw: _stub_result(),
        )
        result = asr.transcribe("fake.wav", engine="whisperx_disfluent")
        assert result["engine"] == "whisperx_disfluent"

    def test_engine_not_overridden_when_already_set(self, monkeypatch):
        def fake(audio_path, **kw):
            r = _stub_result()
            r["engine"] = "custom"
            return r

        monkeypatch.setitem(asr.ENGINES, "whisperx_disfluent", fake)
        result = asr.transcribe("fake.wav", engine="whisperx_disfluent")
        assert result["engine"] == "custom"


class TestDisfluencyPromptDiffersFromDefault:
    def test_disfluency_prompt_is_not_the_clinical_prompt(self):
        assert asr.DISFLUENCY_PROMPT != asr.DEFAULT_PROMPT

    def test_disfluency_prompt_contains_fillers(self):
        # Sanity check on the prompt's own register -- it should actually
        # contain the disfluency vocabulary it's meant to prime.
        lowered = asr.DISFLUENCY_PROMPT.lower()
        assert "uh" in lowered
        assert "um" in lowered
