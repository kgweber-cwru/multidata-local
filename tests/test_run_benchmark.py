"""benchmarks/run_benchmark.py's load_result() branching -- scoring a cached
transcript vs. running an engine live. No real engine/model/audio involved:
the live branch is exercised via a monkeypatched asr.transcribe."""
import argparse
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pympi")  # run_benchmark imports multidata.elan, which needs it

BENCHMARKS = Path(__file__).resolve().parent.parent / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

import run_benchmark  # noqa: E402


def _args(**overrides):
    # engine=None matches the real CLI's argparse default -- None means "the
    # user did not type --engine", distinct from explicitly passing a value
    # that happens to match the default (see load_result's refusal logic).
    defaults = dict(audio="fake.wav", engine=None, model="large-v3", transcript=None)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestLiveMode:
    def test_calls_asr_transcribe_and_reports_source_live(self, monkeypatch):
        def fake_transcribe(audio_path, engine, **kwargs):
            return {"segments": [{"text": "hello"}]}

        monkeypatch.setattr(run_benchmark.asr, "transcribe", fake_transcribe)
        result, engine, source, wall_s = run_benchmark.load_result(_args())
        assert result == {"segments": [{"text": "hello"}]}
        assert engine == "faster_whisper"
        assert source == "live"
        assert wall_s >= 0.0

    def test_suite_engine_gets_no_model_kwarg(self, monkeypatch):
        received = {}

        def fake_transcribe(audio_path, engine, **kwargs):
            received.update(kwargs)
            return {"segments": []}

        monkeypatch.setattr(run_benchmark.asr, "transcribe", fake_transcribe)
        run_benchmark.load_result(_args(engine="suite"))
        assert "model_name" not in received


class TestCachedMode:
    def test_loads_transcript_and_reports_source_cached(self, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"engine": "whisperx_disfluent", "segments": []}))
        result, engine, source, wall_s = run_benchmark.load_result(
            _args(transcript=str(transcript))
        )
        assert result == {"engine": "whisperx_disfluent", "segments": []}
        assert engine == "whisperx_disfluent"
        assert source == "cached"
        assert wall_s == 0.0

    def test_missing_transcript_engine_uses_explicit_arg(self, tmp_path):
        # An older/hand-built JSON with no "engine" key at all -- fine as
        # long as --engine was given explicitly.
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"segments": []}))
        result, engine, source, _ = run_benchmark.load_result(
            _args(transcript=str(transcript), engine="whisperx")
        )
        assert engine == "whisperx"
        assert source == "cached"

    def test_missing_transcript_engine_and_no_explicit_arg_refuses(self, tmp_path):
        # The exact bug this guards against (case 261456): a transcript with
        # no "engine" field, and the user didn't type --engine either --
        # must refuse rather than silently recording asr.DEFAULT_ENGINE.
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"segments": []}))
        with pytest.raises(SystemExit):
            run_benchmark.load_result(_args(transcript=str(transcript), engine=None))

    def test_transcript_engine_overrides_mismatched_arg(self, tmp_path, capsys):
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"engine": "whisperx_disfluent", "segments": []}))
        _, engine, _, _ = run_benchmark.load_result(
            _args(transcript=str(transcript), engine="faster_whisper")
        )
        # The transcript's own recorded engine wins for provenance, even
        # though --engine defaulted/was set to something else.
        assert engine == "whisperx_disfluent"
        assert "note:" in capsys.readouterr().err

    def test_matching_engine_prints_no_note(self, tmp_path, capsys):
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"engine": "faster_whisper", "segments": []}))
        run_benchmark.load_result(_args(transcript=str(transcript), engine="faster_whisper"))
        assert capsys.readouterr().err == ""

    def test_missing_transcript_file_exits(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(SystemExit):
            run_benchmark.load_result(_args(transcript=str(missing)))


class TestFields:
    def test_source_is_in_fields(self):
        assert "source" in run_benchmark.FIELDS
