from multidata import manifest


def _seeded(path, recording_id="999"):
    conn = manifest.connect(path)
    conn.execute("INSERT INTO recordings (recording_id, learner_name, sp_name) VALUES (?, ?, ?)",
                 (recording_id, "Jamie", "Alex"))
    conn.commit()
    conn.close()


class TestRecordGold:
    def test_insert_returns_an_id(self, tmp_path):
        db = tmp_path / "m.sqlite"
        _seeded(db)
        row_id = manifest.record_gold("999", annotator="kate", gold_at="2026-08-26",
                                       path=db)
        assert isinstance(row_id, int)

    def test_multiple_rows_per_recording_allowed(self, tmp_path):
        # Excerpt gold from several spans of the same encounter is a valid
        # strategy (transcription_standards.md §3) -- recording_id is not unique.
        db = tmp_path / "m.sqlite"
        _seeded(db)
        manifest.record_gold("999", span_start=0, span_end=300, path=db)
        manifest.record_gold("999", span_start=600, span_end=900, path=db)
        rows = manifest.gold_for_recording("999", path=db)
        assert len(rows) == 2

    def test_reannotation_adds_a_row_not_an_update(self, tmp_path):
        db = tmp_path / "m.sqlite"
        _seeded(db)
        manifest.record_gold("999", pass1_frozen_at="2026-08-01", path=db)
        manifest.record_gold("999", pass1_frozen_at="2026-08-15", path=db)
        rows = manifest.gold_for_recording("999", path=db)
        assert [r["pass1_frozen_at"] for r in rows] == ["2026-08-01", "2026-08-15"]


class TestGoldForRecording:
    def test_empty_for_recording_with_no_gold(self, tmp_path):
        db = tmp_path / "m.sqlite"
        _seeded(db)
        assert manifest.gold_for_recording("999", path=db) == []

    def test_only_returns_matching_recording(self, tmp_path):
        db = tmp_path / "m.sqlite"
        _seeded(db, "999")
        _seeded(db, "888")
        manifest.record_gold("999", path=db)
        manifest.record_gold("888", path=db)
        rows = manifest.gold_for_recording("999", path=db)
        assert len(rows) == 1
        assert rows[0]["recording_id"] == "999"


class TestAllGold:
    def test_which_recordings_have_gold_is_a_query(self, tmp_path):
        db = tmp_path / "m.sqlite"
        _seeded(db, "999")
        _seeded(db, "888")
        manifest.record_gold("999", path=db)
        recordings_with_gold = {row["recording_id"] for row in manifest.all_gold(path=db)}
        assert recordings_with_gold == {"999"}

    def test_empty_manifest_has_no_gold(self, tmp_path):
        db = tmp_path / "m.sqlite"
        manifest.connect(db).close()
        assert manifest.all_gold(path=db) == []


class TestSpanFields:
    def test_whole_encounter_gold_has_null_spans(self, tmp_path):
        db = tmp_path / "m.sqlite"
        _seeded(db)
        manifest.record_gold("999", path=db)
        row = manifest.gold_for_recording("999", path=db)[0]
        assert row["span_start"] is None
        assert row["span_end"] is None

    def test_excerpt_gold_records_span(self, tmp_path):
        db = tmp_path / "m.sqlite"
        _seeded(db)
        manifest.record_gold("999", span_start=0.0, span_end=300.0, path=db)
        row = manifest.gold_for_recording("999", path=db)[0]
        assert row["span_start"] == 0.0
        assert row["span_end"] == 300.0
