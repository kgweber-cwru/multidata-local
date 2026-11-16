from multidata import manifest


def _seeded(path, recording_id="999"):
    conn = manifest.connect(path)
    conn.execute("INSERT INTO recordings (recording_id, learner_name, sp_name) VALUES (?, ?, ?)",
                 (recording_id, "Jamie", "Alex"))
    conn.commit()
    conn.close()


def test_every_gold_export_adds_a_row(tmp_path):
    # Excerpts of one encounter and re-annotations are both valid
    # (transcription_standards.md §3), so recording_id is not unique and a
    # second export never overwrites the first.
    db = tmp_path / "m.sqlite"
    _seeded(db, "999")
    _seeded(db, "888")
    manifest.record_gold("999", path=db)
    manifest.record_gold("999", span_start=0.0, span_end=300.0, path=db)
    rows = manifest.all_gold(path=db)
    assert [r["recording_id"] for r in rows] == ["999", "999"]
    assert (rows[0]["span_start"], rows[0]["span_end"]) == (None, None)
    assert (rows[1]["span_start"], rows[1]["span_end"]) == (0.0, 300.0)


def test_empty_manifest_has_no_gold(tmp_path):
    db = tmp_path / "m.sqlite"
    manifest.connect(db).close()
    assert manifest.all_gold(path=db) == []
