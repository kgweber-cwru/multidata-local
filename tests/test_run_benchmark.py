"""benchmarks/run_benchmark.py: scoring saved transcripts into the ledger.
No engine runs here; transcripts are small JSON files written by the test."""
import csv
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pympi")  # run_benchmark imports multidata.elan, which needs it

BENCHMARKS = Path(__file__).resolve().parent.parent / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))

import run_benchmark  # noqa: E402


def _transcript(path, text, provenance=None, engine=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"segments": [{"start": 0.0, "end": 1.0, "text": text}]}
    if provenance:
        record["provenance"] = provenance
    if engine:
        record["engine"] = engine
    path.write_text(json.dumps(record))
    return path


class TestScoreTranscript:
    def _gold(self, refs, text="so tell me about it", meta=None):
        refs.mkdir(exist_ok=True)
        (refs / "999.gold.txt").write_text(text)
        if meta:
            (refs / "999.gold.json").write_text(json.dumps(meta))
        return refs

    def test_row_names_engine_model_and_setting(self, tmp_path):
        refs = self._gold(tmp_path / "refs")
        t = _transcript(tmp_path / "999" / "variants" / "assemblyai_medical" / "16_assemblyai_u3.json",
                        "um so tell me about it",
                        provenance={"engine": "assemblyai", "model": "u3", "wall_s": 12.0})
        ledger = tmp_path / "runs.csv"
        row = run_benchmark.score_transcript(t, "999", results_path=ledger, references=refs,
                                             manifest_path=tmp_path / "m.sqlite")
        assert (row["engine"], row["model"], row["setting"]) == ("assemblyai", "u3", "assemblyai_medical")
        assert row["wer"] > 0 and row["wer_l1"] == 0.0 and row["wall_s"] == 12.0
        assert list(csv.DictReader(open(ledger)))[0]["transcript"] == row["transcript"]

    def test_old_transcript_takes_its_model_from_the_file_name(self, tmp_path):
        refs = self._gold(tmp_path / "refs")
        t = _transcript(tmp_path / "999" / "16_whisperx_disfluent_large-v3.json", "so tell me about it",
                        engine="whisperx_disfluent")
        row = run_benchmark.score_transcript(t, "999", results_path=tmp_path / "runs.csv",
                                             references=refs, manifest_path=tmp_path / "m.sqlite")
        assert (row["engine"], row["model"], row["setting"]) == ("whisperx_disfluent", "large-v3", "")

    def test_seed_engine_is_not_scored_on_its_own_gold(self, tmp_path):
        refs = self._gold(tmp_path / "refs", meta={"recording_id": "999", "pass1_source": "seeded",
                                                   "seed_engine": "whisperx_disfluent"})
        t = _transcript(tmp_path / "999" / "16_whisperx_disfluent_large-v3.json", "x",
                        engine="whisperx_disfluent")
        out = run_benchmark.score_transcript(t, "999", results_path=tmp_path / "runs.csv",
                                             references=refs, manifest_path=tmp_path / "m.sqlite")
        assert out.startswith("not scored") and not (tmp_path / "runs.csv").exists()

    def test_excerpt_gold_scores_only_its_span(self, tmp_path):
        refs = self._gold(tmp_path / "refs", text="during",
                          meta={"recording_id": "999", "span": [4.0, 7.0]})
        t = tmp_path / "999" / "16_assemblyai_u3.json"
        t.parent.mkdir()
        t.write_text(json.dumps({"provenance": {"engine": "assemblyai", "model": "u3"}, "segments": [
            {"start": 0, "end": 10, "text": "before during after", "words": [
                {"word": "before", "start": 1.0, "end": 2.0}, {"word": "during", "start": 5.0, "end": 6.0},
                {"word": "after", "start": 9.0, "end": 10.0}]}]}))
        row = run_benchmark.score_transcript(t, "999", results_path=tmp_path / "runs.csv",
                                             references=refs, manifest_path=tmp_path / "m.sqlite")
        assert row["wer"] == 0.0


def test_scored_already_counts_only_the_current_method(tmp_path):
    ledger = tmp_path / "runs.csv"
    row = {f: "" for f in run_benchmark.FIELDS}
    run_benchmark.record(row | {"transcript": "a.json", "gold_sha256": "g", "scoring": run_benchmark.SCORING},
                         results_path=ledger)
    run_benchmark.record(row | {"transcript": "b.json", "gold_sha256": "g", "scoring": "old"},
                         results_path=ledger)
    assert run_benchmark.scored_already(ledger) == {("a.json", "g")}


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


class TestRecord:
    """record()'s results_path override -- exists specifically so a test or
    smoke run never touches the real runs.csv. It didn't exist for a while;
    a manual smoke test wrote synthetic rows into the real ledger twice
    before this was added (see the module's docstring on `record`)."""

    def test_writes_header_and_row_on_first_call(self, tmp_path):
        target = tmp_path / "runs.csv"
        run_benchmark.record({"recording_id": "x"} | {f: "" for f in run_benchmark.FIELDS
                                                  if f != "recording_id"},
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

class TestScore:
    """score(): names redacted on both sides, then the designed L0/L1
    normalization, so tags and spoken names are neutral."""

    NAMES = {"learner": "Jamie Smith", "patient": "Alex Rivera", "preceptor": ""}

    def test_name_in_hypothesis_matches_redacted_gold(self):
        ref = "Hi, I'm [LEARNER_NAME]. Nice to meet you."
        hyp = "hi im jamie smith nice to meet you"
        assert run_benchmark.score(ref, hyp, self.NAMES)["wer"] == 0.0

    def test_name_in_both_sides_is_neutral(self):
        ref = "Hi, I'm Jamie Smith."
        hyp = "Hi I'm Jamie Smyth"
        assert run_benchmark.score(ref, hyp, self.NAMES)["wer"] == 0.0

    def test_l1_ignores_fillers_l0_does_not(self):
        ref = "um so tell me about it"
        hyp = "so tell me about it"
        s = run_benchmark.score(ref, hyp, self.NAMES)
        assert s["wer"] > 0 and s["wer_l1"] == 0.0

    def test_counts_behind_the_rates(self):
        ref = "um so tell me about it"
        hyp = "so tell me about that mm-hmm uh"
        s = run_benchmark.score(ref, hyp, self.NAMES)
        assert (s["words"], s["sub"], s["del"], s["ins"]) == (6, 1, 1, 2)
        assert s["wer"] == round((s["sub"] + s["del"] + s["ins"]) / s["words"], 4)
        assert (s["words_l1"], s["errors_l1"]) == (5, 2) and s["wer_l1"] == round(s["errors_l1"] / s["words_l1"], 4)
        assert (s["fillers"], s["backchannels"]) == (1, 1)


class TestSpeakers:
    RESULT = {"segments": [{"words": [
        {"word": "hi", "start": 0.0, "end": 0.5, "speaker": "A"},
        {"word": "there", "start": 0.6, "end": 1.0, "speaker": "A"},
        {"word": "[echoing]", "start": 1.0, "end": 200.0, "speaker": "A"},
        {"word": "hello", "start": 1.2, "end": 2.0, "speaker": "B"},
        {"word": "Tan.", "start": 3.0, "end": 160.0, "speaker": "B"},
        {"word": "bye", "start": 5.0, "end": 5.5, "speaker": "A"},
    ]}]}

    def test_turns_merge_words_and_skip_tags_and_glitches(self):
        assert run_benchmark.speaker_turns(self.RESULT) == [
            (0.0, 1.0, "A"), (1.2, 2.0, "B"), (5.0, 5.5, "A")]

    def test_matching_turns_score_zero_whatever_the_labels(self):
        ref = [(0.0, 10.0, "LEARNER"), (12.0, 20.0, "PATIENT"), (25.0, 30.0, "LEARNER")]
        hyp = [(0.0, 10.0, "A"), (12.0, 20.0, "B"), (25.0, 30.0, "A")]
        # speech_s: 23 s of gold speech less the 0.5 s collar inside each of 6 boundaries
        assert run_benchmark.speaker_scores(ref, hyp, (0.0, 30.0)) == {
            "der": 0.0, "jer": 0.0, "speech_s": 20.0}
        swapped = [(0.0, 10.0, "A"), (12.0, 20.0, "A"), (25.0, 30.0, "A")]
        assert run_benchmark.speaker_scores(ref, swapped, (0.0, 30.0))["der"] > 0

    def test_cpwer_matches_speakers_and_counts_a_merge(self):
        ref = {"LEARNER": "how are you", "PATIENT": "fine thanks"}
        assert run_benchmark.cpwer(ref, {"S1": "fine thanks", "S0": "how are you"}) == 0.0
        # one engine speaker holding both people's words: the unmatched role is all missed
        assert run_benchmark.cpwer(ref, {"S0": "how are you fine thanks"}) == 0.8
