"""Roster rows in the manifest: recordings known only from the LearningSpace
API, keyed by GUID until they are exported (docs/learningspace.md)."""
import pytest

from multidata import manifest
from multidata.models import Recording

from test_learningspace import load_script, make_db, FakeClient
from multidata import learningspace as ls

ROSTER = {"ls_recording_id": "rec1", "ls_activity_id": "act1", "ls_event_id": "77",
          "ls_case_id": "scen1", "ls_learner_id": "stu1", "duration_s": 1208.0,
          "n_files": 2, "ls_preceptor_id": "obs1",
          "recording_start_utc": "2026-02-26T21:22:04Z"}


def test_upsert_adds_a_guid_keyed_row(tmp_path):
    db = make_db(tmp_path)
    assert manifest.upsert_ls_recording(ROSTER, db) == ("added", "rec1", [])
    r = manifest.get_recording("rec1", db)
    assert r.is_roster and r.duration_s == 1208.0 and r.n_files == 2
    assert not manifest.get_recording("900001", db).is_roster


def test_roster_rows_hidden_unless_asked(tmp_path):
    db = make_db(tmp_path)
    manifest.upsert_ls_recording(ROSTER, db)
    assert "rec1" not in {r.recording_id for r in manifest.all_recordings(db)}
    assert "rec1" in {r.recording_id for r in manifest.all_recordings(db, roster=True)}


def test_upsert_fills_an_exported_row_and_keeps_conflicts(tmp_path):
    db = make_db(tmp_path)
    manifest.update_recording("900001", db, ls_recording_id="rec1", ls_event_id="76")
    status, rid, conflicts = manifest.upsert_ls_recording(ROSTER, db)
    assert (status, rid) == ("filled", "900001")
    assert conflicts == [("ls_event_id", "76", "77")]
    r = manifest.get_recording("900001", db)
    assert r.duration_s == 1208.0 and r.ls_event_id == "76"
    assert manifest.upsert_ls_recording(ROSTER, db)[0] == "unchanged"
    assert manifest.get_recording("rec1", db) is None


@pytest.mark.parametrize("bad", [{"learner_name": "X"}, {"preceptor_name": "X"},
                                 {"recording_id": "1"}, {"nope": 1}])
def test_upsert_refuses_names_and_unknown_columns(tmp_path, bad):
    with pytest.raises(ValueError):
        manifest.upsert_ls_recording({**ROSTER, **bad}, make_db(tmp_path))


def test_guid_is_unique(tmp_path):
    db = make_db(tmp_path)
    manifest.upsert_ls_recording(ROSTER, db)
    with pytest.raises(Exception):
        manifest.update_recording("900001", db, ls_recording_id="rec1")


def test_absorb_moves_values_and_frees_the_guid(tmp_path):
    db = make_db(tmp_path)
    manifest.upsert_ls_recording(ROSTER, db)
    copied = manifest.absorb_roster_row("900001", "rec1", db)
    assert "duration_s" in copied and "ls_recording_id" in copied
    assert manifest.get_recording("rec1", db) is None
    r = manifest.get_recording("900001", db)
    assert r.ls_recording_id == "rec1" and r.n_files == 2
    assert manifest.absorb_roster_row("900001", "rec1", db) is None


def test_prune_spares_roster_and_linked_rows(tmp_path):
    db = make_db(tmp_path)
    manifest.upsert_ls_recording(ROSTER, db)
    manifest.update_recording("900002", db, ls_recording_id="rec9")
    assert manifest.prune_recordings_without_videos(db) == []


def test_backfill_absorbs_the_roster_row(tmp_path, monkeypatch, capsys):
    db = make_db(tmp_path)
    manifest.upsert_ls_recording({**ROSTER, "ls_preceptor_id": ""}, db)
    monkeypatch.setattr(ls.Client, "from_env", classmethod(lambda cls, **kw: FakeClient()))
    load_script().main(["--manifest", str(db), "--apply"])
    assert "absorbs the roster row" in capsys.readouterr().out
    assert manifest.get_recording("rec1", db) is None
    r = manifest.get_recording("900001", db)
    assert (r.ls_recording_id, r.duration_s) == ("rec1", 1208.0)
    assert r.ls_preceptor_id   # from the observer's SubmitterID


def test_resolve_takes_the_event_from_the_event_file_lists():
    """Two events on one day: the per-pair endpoint stamps each recording
    with whichever event was asked for, so the event comes from the file lists."""
    from test_learningspace import EVENT, REC, recording_row, video_rows

    second = {**EVENT, "ID": 78, "Title": "Exam Day, afternoon"}

    class TwoEvents(FakeClient):
        def recordings(self, event_id, case_id, student_id):
            return [{**REC, "EventID": event_id}]      # the echo

        def event_files(self, event_id):
            return [{"VideoRecordingID": "rec1"}] if event_id == 78 else []

    res = ls.resolve(recording_row(), video_rows(), TwoEvents(events=[EVENT, second]))
    assert res.linked and res.recording_fields["ls_event_id"] == "78"
