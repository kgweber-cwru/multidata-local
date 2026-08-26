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
        result, engine, source, wall_s = run_benchmark.load_result(_args(), "fakehash")
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
        run_benchmark.load_result(_args(engine="suite"), "fakehash")
        assert "model_name" not in received


class TestCaching:
    """The engine should never run twice for the same (audio, engine, model)
    -- caching is the difference between iterating for free and iterating on
    the meter (implementation plan Phase 2.2)."""

    def test_cache_miss_calls_engine_and_populates_cache(self, tmp_path, monkeypatch):
        calls = []

        def fake_transcribe(audio_path, engine, **kwargs):
            calls.append(1)
            return {"segments": [{"text": "hello"}]}

        monkeypatch.setattr(run_benchmark.asr, "transcribe", fake_transcribe)
        result, engine, source, wall_s = run_benchmark.load_result(
            _args(), "fakehash", cache_root=tmp_path
        )
        assert len(calls) == 1
        assert source == "live"
        assert result == {"segments": [{"text": "hello"}]}

    def test_cache_hit_skips_engine_entirely(self, tmp_path, monkeypatch):
        calls = []

        def fake_transcribe(audio_path, engine, **kwargs):
            calls.append(1)
            return {"segments": [{"text": "hello"}]}

        monkeypatch.setattr(run_benchmark.asr, "transcribe", fake_transcribe)
        run_benchmark.load_result(_args(), "fakehash", cache_root=tmp_path)
        result, engine, source, wall_s = run_benchmark.load_result(
            _args(), "fakehash", cache_root=tmp_path
        )
        assert len(calls) == 1  # second call was a cache hit, not a second transcribe
        assert source == "cache_hit"
        assert wall_s == 0.0
        assert result == {"segments": [{"text": "hello"}]}

    def test_different_model_is_a_different_cache_entry(self, tmp_path, monkeypatch):
        calls = []

        def fake_transcribe(audio_path, engine, **kwargs):
            calls.append(kwargs.get("model_name"))
            return {"segments": []}

        monkeypatch.setattr(run_benchmark.asr, "transcribe", fake_transcribe)
        run_benchmark.load_result(_args(model="medium"), "fakehash", cache_root=tmp_path)
        run_benchmark.load_result(_args(model="large-v3"), "fakehash", cache_root=tmp_path)
        assert calls == ["medium", "large-v3"]

    def test_no_cache_root_always_runs_live(self, monkeypatch):
        calls = []

        def fake_transcribe(audio_path, engine, **kwargs):
            calls.append(1)
            return {"segments": []}

        monkeypatch.setattr(run_benchmark.asr, "transcribe", fake_transcribe)
        run_benchmark.load_result(_args(), "fakehash", cache_root=None)
        run_benchmark.load_result(_args(), "fakehash", cache_root=None)
        assert len(calls) == 2


class TestTranscriptMode:
    def test_loads_transcript_and_reports_source_cached(self, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"engine": "whisperx_disfluent", "segments": []}))
        result, engine, source, wall_s = run_benchmark.load_result(
            _args(transcript=str(transcript)), "fakehash"
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
            _args(transcript=str(transcript), engine="whisperx"), "fakehash"
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
            run_benchmark.load_result(_args(transcript=str(transcript), engine=None), "fakehash")

    def test_transcript_engine_overrides_mismatched_arg(self, tmp_path, capsys):
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"engine": "whisperx_disfluent", "segments": []}))
        _, engine, _, _ = run_benchmark.load_result(
            _args(transcript=str(transcript), engine="faster_whisper"), "fakehash"
        )
        # The transcript's own recorded engine wins for provenance, even
        # though --engine defaulted/was set to something else.
        assert engine == "whisperx_disfluent"
        assert "note:" in capsys.readouterr().err

    def test_matching_engine_prints_no_note(self, tmp_path, capsys):
        transcript = tmp_path / "t.json"
        transcript.write_text(json.dumps({"engine": "faster_whisper", "segments": []}))
        run_benchmark.load_result(
            _args(transcript=str(transcript), engine="faster_whisper"), "fakehash"
        )
        assert capsys.readouterr().err == ""

    def test_missing_transcript_file_exits(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(SystemExit):
            run_benchmark.load_result(_args(transcript=str(missing)), "fakehash")


class TestFields:
    def test_source_is_in_fields(self):
        assert "source" in run_benchmark.FIELDS


class TestHypothesisTextSpan:
    """Excerpt gold (transcription_standards.md §3) covers only part of the
    audio; the hypothesis normally covers all of it. Scoring the whole
    hypothesis against an excerpt reference inflates WER with spurious
    insertions for every word outside the excerpt -- span filtering is what
    makes span_start/span_end (manifest.record_gold) load-bearing rather than
    decorative."""

    WORDY_RESULT = {
        "segments": [{
            "start": 0.0, "end": 10.0, "text": "before during after",
            "words": [
                {"word": "before", "start": 1.0, "end": 2.0},
                {"word": "during", "start": 5.0, "end": 6.0},
                {"word": "after", "start": 9.0, "end": 10.0},
            ],
        }],
    }

    def test_no_span_returns_everything(self):
        assert run_benchmark.hypothesis_text(self.WORDY_RESULT) == "before during after"

    def test_span_filters_to_window(self):
        text = run_benchmark.hypothesis_text(self.WORDY_RESULT, span=(4.0, 7.0))
        assert text == "during"

    def test_span_covering_everything_is_unfiltered(self):
        text = run_benchmark.hypothesis_text(self.WORDY_RESULT, span=(0.0, 100.0))
        assert text == "before during after"

    def test_span_covering_nothing_is_empty(self):
        text = run_benchmark.hypothesis_text(self.WORDY_RESULT, span=(50.0, 60.0))
        assert text == ""

    def test_falls_back_to_segment_level_without_word_timing(self):
        result = {
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "before"},
                {"start": 5.0, "end": 10.0, "text": "during"},
                {"start": 10.0, "end": 15.0, "text": "after"},
            ],
        }
        text = run_benchmark.hypothesis_text(result, span=(4.0, 7.0))
        assert text == "during"


class TestSpanCliValidation:
    def test_span_start_without_end_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", [
            "run_benchmark.py", "--case", "x", "--audio", "fake.wav", "--span-start", "1.0",
        ])
        with pytest.raises(SystemExit):
            run_benchmark.main()


class TestRecord:
    """record()'s results_path override -- exists specifically so a test or
    smoke run never touches the real runs.csv. It didn't exist for a while;
    a manual smoke test wrote synthetic rows into the real ledger twice
    before this was added (see the module's docstring on `record`)."""

    def test_writes_header_and_row_on_first_call(self, tmp_path):
        target = tmp_path / "runs.csv"
        run_benchmark.record({"case_id": "x"} | {f: "" for f in run_benchmark.FIELDS
                                                  if f != "case_id"},
                              results_path=target)
        lines = target.read_text().splitlines()
        assert lines[0] == ",".join(run_benchmark.FIELDS)
        assert "x" in lines[1]

    def test_second_call_appends_without_a_second_header(self, tmp_path):
        target = tmp_path / "runs.csv"
        row = {f: "" for f in run_benchmark.FIELDS}
        run_benchmark.record(row, results_path=target)
        run_benchmark.record(row, results_path=target)
        lines = target.read_text().splitlines()
        assert lines.count(",".join(run_benchmark.FIELDS)) == 1
        assert len(lines) == 3

    def test_default_results_path_is_the_real_ledger(self):
        assert run_benchmark.RESULTS.name == "runs.csv"
