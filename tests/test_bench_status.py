"""scripts/bench_status.py -- gold_status/read_runs/latest_per_config. Pure
file/manifest reads, no engine or model involved."""
import csv
import sys
from pathlib import Path

import pytest

pytest.importorskip("pympi")  # bench_status imports multidata.elan transitively via asr

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "benchmarks"))

import bench_status  # noqa: E402

from multidata import manifest  # noqa: E402


def _seed_recording(db, recording_id="999"):
    conn = manifest.connect(db)
    conn.execute("INSERT INTO recordings (recording_id, learner_name, sp_name) VALUES (?, ?, ?)",
                 (recording_id, "Jamie", "Alex"))
    conn.commit()
    conn.close()


class TestGoldStatus:
    def test_manifest_row_with_no_file_is_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        db = tmp_path / "m.sqlite"
        _seed_recording(db)
        manifest.record_gold("999", path=db)
        [entry] = bench_status.gold_status(db)
        assert entry["recording_id"] == "999"
        assert entry["txt_exists"] is False
        assert entry["manifest_rows"]

    def test_file_with_no_manifest_row_is_flagged(self, tmp_path, monkeypatch):
        # The actual state recording 261456 was found in: exported by an older
        # eaf_to_gold.py that predated the gold-table wiring.
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        (tmp_path / "261456.gold.txt").write_text("hello")
        db = tmp_path / "m.sqlite"
        manifest.connect(db).close()
        [entry] = bench_status.gold_status(db)
        assert entry["recording_id"] == "261456"
        assert entry["manifest_rows"] == []
        assert entry["txt_exists"] is True

    def test_both_present_is_unflagged_in_practice(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        (tmp_path / "999.gold.txt").write_text("hello")
        (tmp_path / "999.gold.rttm").write_text("SPEAKER ...")
        db = tmp_path / "m.sqlite"
        _seed_recording(db)
        manifest.record_gold("999", path=db)
        [entry] = bench_status.gold_status(db)
        assert entry["txt_exists"] and entry["rttm_exists"] and entry["manifest_rows"]

    def test_excerpt_spans_both_show_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        db = tmp_path / "m.sqlite"
        _seed_recording(db)
        manifest.record_gold("999", span_start=0, span_end=300, path=db)
        manifest.record_gold("999", span_start=600, span_end=900, path=db)
        [entry] = bench_status.gold_status(db)
        assert len(entry["manifest_rows"]) == 2


class TestReadRuns:
    def test_missing_file_returns_empty(self, tmp_path):
        assert bench_status.read_runs(tmp_path / "nope.csv") == []

    def test_reads_real_rows(self, tmp_path):
        path = tmp_path / "runs.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["recording_id", "engine", "timestamp"])
            writer.writeheader()
            writer.writerow({"recording_id": "999", "engine": "whisperx", "timestamp": "t1"})
        rows = bench_status.read_runs(path)
        assert rows == [{"recording_id": "999", "engine": "whisperx", "timestamp": "t1"}]


class TestLatestPerConfig:
    def test_picks_most_recent_by_timestamp(self):
        runs = [
            {"recording_id": "999", "engine": "whisperx", "model": "medium",
             "timestamp": "2026-01-01", "wer": "0.5"},
            {"recording_id": "999", "engine": "whisperx", "model": "medium",
             "timestamp": "2026-06-01", "wer": "0.3"},
        ]
        latest = bench_status.latest_per_config(runs)
        assert latest[("999", "whisperx", "medium", "")]["wer"] == "0.3"

    def test_different_configs_kept_separate(self):
        runs = [
            {"recording_id": "999", "engine": "whisperx", "model": "medium", "timestamp": "t1"},
            {"recording_id": "999", "engine": "whisperx_disfluent", "model": "medium",
             "timestamp": "t1"},
        ]
        latest = bench_status.latest_per_config(runs)
        assert len(latest) == 2


class TestBenchStatusCurrent:
    def test_only_current_method_and_gold_count(self, tmp_path):
        import run_benchmark
        (tmp_path / "1.gold.txt").write_text("hello")
        sha = run_benchmark.sha256(tmp_path / "1.gold.txt")
        ok = {"recording_id": "1", "scoring": run_benchmark.SCORING, "gold_sha256": sha}
        assert bench_status.is_current(ok, tmp_path)
        assert not bench_status.is_current(ok | {"scoring": ""}, tmp_path)
        assert not bench_status.is_current(ok | {"gold_sha256": "old"}, tmp_path)
        assert not bench_status.is_current(ok | {"recording_id": "2"}, tmp_path)


class TestPooled:
    def test_rates_weighted_by_their_own_denominators(self, tmp_path):
        (tmp_path / "1.gold.txt").write_text("um mm-hmm yes")
        (tmp_path / "2.gold.txt").write_text("uh-uh no")
        base = {"engine": "e", "model": "m", "setting": "", "gold_pass1": "blind"}
        latest = {
            ("1", "e", "m", ""): base | {
                "recording_id": "1", "wer": "0.1", "words": "100", "sub": "5", "del": "3",
                "ins": "2", "words_l1": "90", "errors_l1": "9", "der": "0.2", "speech_s": "10",
                "cp_words": "100", "cp_errors": "20", "fillers": "4", "backchannels": "1"},
            ("2", "e", "m", ""): base | {
                "recording_id": "2", "wer": "0.4", "words": "300", "sub": "60", "del": "40",
                "ins": "20", "words_l1": "310", "errors_l1": "31", "der": "0.5", "speech_s": "30",
                "cp_words": "300", "cp_errors": "60", "fillers": "0", "backchannels": "2"},
        }
        [e] = bench_status.pooled(latest, tmp_path)
        assert e["recordings"] == 2
        assert e["wer"] == pytest.approx(130 / 400)
        assert (e["sub"], e["del"], e["ins"]) == pytest.approx((65 / 400, 43 / 400, 22 / 400))
        assert e["wer_l1"] == pytest.approx(40 / 400)
        assert e["range"] == (0.1, 0.4)
        assert e["cpwer"] == pytest.approx(80 / 400)
        assert e["der"] == pytest.approx((0.2 * 10 + 0.5 * 30) / 40)
        assert e["fillers"] == (4, 1) and e["backchannels"] == (3, 2)

    def test_rows_without_counts_are_left_out(self, tmp_path):
        latest = {("1", "e", "m", ""): {"recording_id": "1", "engine": "e", "model": "m",
                                        "wer": "0.1", "words": ""}}
        assert bench_status.pooled(latest, tmp_path) == []
