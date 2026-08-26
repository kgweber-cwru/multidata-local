"""scripts/bench_status.py -- gold_status/read_runs/latest_per_config. Pure
file/manifest reads, no engine or model involved."""
import csv
import sys
from pathlib import Path

import pytest

pytest.importorskip("pympi")  # bench_status imports multidata.elan transitively via asr

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bench_status  # noqa: E402

from multidata import manifest  # noqa: E402


def _seed_case(db, case_id="999"):
    conn = manifest.connect(db)
    conn.execute("INSERT INTO cases (case_id, learner_name, sp_name) VALUES (?, ?, ?)",
                 (case_id, "Jamie", "Alex"))
    conn.commit()
    conn.close()


class TestGoldStatus:
    def test_manifest_row_with_no_file_is_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        db = tmp_path / "m.sqlite"
        _seed_case(db)
        manifest.record_gold("999", path=db)
        [entry] = bench_status.gold_status(db)
        assert entry["case_id"] == "999"
        assert entry["txt_exists"] is False
        assert entry["manifest_rows"]

    def test_file_with_no_manifest_row_is_flagged(self, tmp_path, monkeypatch):
        # The actual state case 261456 was found in: exported by an older
        # eaf_to_gold.py that predated the gold-table wiring.
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        (tmp_path / "261456.gold.txt").write_text("hello")
        db = tmp_path / "m.sqlite"
        manifest.connect(db).close()
        [entry] = bench_status.gold_status(db)
        assert entry["case_id"] == "261456"
        assert entry["manifest_rows"] == []
        assert entry["txt_exists"] is True

    def test_both_present_is_unflagged_in_practice(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        (tmp_path / "999.gold.txt").write_text("hello")
        (tmp_path / "999.gold.rttm").write_text("SPEAKER ...")
        db = tmp_path / "m.sqlite"
        _seed_case(db)
        manifest.record_gold("999", path=db)
        [entry] = bench_status.gold_status(db)
        assert entry["txt_exists"] and entry["rttm_exists"] and entry["manifest_rows"]

    def test_excerpt_spans_both_show_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bench_status, "REFERENCES", tmp_path)
        db = tmp_path / "m.sqlite"
        _seed_case(db)
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
            writer = csv.DictWriter(f, fieldnames=["case_id", "engine", "timestamp"])
            writer.writeheader()
            writer.writerow({"case_id": "999", "engine": "whisperx", "timestamp": "t1"})
        rows = bench_status.read_runs(path)
        assert rows == [{"case_id": "999", "engine": "whisperx", "timestamp": "t1"}]


class TestLatestPerConfig:
    def test_picks_most_recent_by_timestamp(self):
        runs = [
            {"case_id": "999", "engine": "whisperx", "model": "medium",
             "timestamp": "2026-01-01", "wer": "0.5"},
            {"case_id": "999", "engine": "whisperx", "model": "medium",
             "timestamp": "2026-06-01", "wer": "0.3"},
        ]
        latest = bench_status.latest_per_config(runs)
        assert latest[("999", "whisperx", "medium")]["wer"] == "0.3"

    def test_different_configs_kept_separate(self):
        runs = [
            {"case_id": "999", "engine": "whisperx", "model": "medium", "timestamp": "t1"},
            {"case_id": "999", "engine": "whisperx_disfluent", "model": "medium",
             "timestamp": "t1"},
        ]
        latest = bench_status.latest_per_config(runs)
        assert len(latest) == 2
