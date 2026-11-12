"""The assignments table (multidata.manifest) and annotation/assignments.py:
returning a pass, and `check`, which compares the table with the kits and
gold files on disk."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "annotation"))

from test_submit_checks import eaf  # noqa: E402

from multidata import manifest  # noqa: E402
import assignments  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return tmp_path / "m.sqlite"


def kit(kits, recording, who, pass_no=1, text="hello there", **kit_json):
    d = kits / recording / who
    d.mkdir(parents=True)
    f = d / f"{recording}.pass{pass_no}.eaf"
    f.write_text(eaf([("LEARNER", 0, 1000, text)]))
    (d / "kit.json").write_text(json.dumps({"recording_id": recording, "annotator": who,
                                            "camera": "7", **kit_json}))
    return f


class TestTable:
    def test_add_get_update(self, db):
        manifest.add_assignment("999", "ann", 1, "blind", db, camera="7")
        manifest.update_assignment("999", "ann", db, status="returned", returned_on="2026-11-01")
        row = manifest.get_assignment("999", "ann", db)
        assert row["pass"] == 1 and row["status"] == "returned" and row["camera"] == "7"

    def test_one_row_per_recording_and_annotator(self, db):
        manifest.add_assignment("999", "ann", 1, "blind", db)
        with pytest.raises(ValueError):
            manifest.add_assignment("999", "ann", 2, "blind", db)

    def test_bad_status_and_unknown_row_refused(self, db):
        manifest.add_assignment("999", "ann", 1, "blind", db)
        with pytest.raises(ValueError):
            manifest.update_assignment("999", "ann", db, status="done")
        with pytest.raises(KeyError):
            manifest.update_assignment("999", "bob", db, status="returned")

    def test_export_import_round_trip(self, db, tmp_path):
        manifest.add_assignment("999", "ann", 1, "seeded", db, notes='a "quoted", note')
        manifest.add_assignment("999", "rev", 2, "seeded", db)
        out = manifest.export_assignments(tmp_path / "a.csv", db)
        other = tmp_path / "n.sqlite"
        assert manifest.import_assignments(out, other) == 2
        assert manifest.all_assignments(other) == manifest.all_assignments(db)
        with pytest.raises(ValueError):
            manifest.import_assignments(out, other)


class TestReturn:
    def test_return_marks_today(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(assignments, "KITS", tmp_path)
        kit(tmp_path, "999", "ann")
        manifest.add_assignment("999", "ann", 1, "blind", db)
        assignments.main(["--manifest", str(db), "return", "999", "ann"])
        row = manifest.get_assignment("999", "ann", db)
        assert row["status"] == "returned" and row["returned_on"] == assignments.today()

    def test_failing_file_not_returned(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(assignments, "KITS", tmp_path)
        kit(tmp_path, "999", "ann", text="")
        manifest.add_assignment("999", "ann", 1, "blind", db)
        with pytest.raises(SystemExit):
            assignments.main(["--manifest", str(db), "return", "999", "ann"])
        assert manifest.get_assignment("999", "ann", db)["status"] == "assigned"

    def test_note_appends(self, db):
        manifest.add_assignment("999", "ann", 1, "blind", db, notes="First.")
        assignments.main(["--manifest", str(db), "note", "999", "ann", "Second."])
        assert manifest.get_assignment("999", "ann", db)["notes"] == "First. Second."


class TestAudit:
    def audit(self, db, tmp_path):
        return assignments.audit(db, kits=tmp_path / "kits", gold=tmp_path / "gold")

    def test_consistent(self, db, tmp_path):
        kit(tmp_path / "kits", "999", "ann")
        manifest.add_assignment("999", "ann", 1, "blind", db, camera="7")
        assert self.audit(db, tmp_path) == []

    def test_kit_without_row_and_row_without_kit(self, db, tmp_path):
        kit(tmp_path / "kits", "999", "ann")
        manifest.add_assignment("998", "bob", 1, "blind", db)
        found = self.audit(db, tmp_path)
        assert any("999 ann: kit folder" in f for f in found)
        assert any("998 bob pass 1: no " in f for f in found)

    def test_abandoned_row_needs_no_kit(self, db, tmp_path):
        manifest.add_assignment("998", "bob", 1, "blind", db, status="abandoned")
        assert self.audit(db, tmp_path) == []

    def test_returned_file_failing_checks(self, db, tmp_path):
        kit(tmp_path / "kits", "999", "ann", text="")
        manifest.add_assignment("999", "ann", 1, "blind", db, status="returned",
                                returned_on="2099-01-01")
        assert any("fails submit_checks" in f for f in self.audit(db, tmp_path))

    def test_frozen_file_edited_after_return(self, db, tmp_path):
        f = kit(tmp_path / "kits", "999", "ann")
        os.utime(f, (1.9e9, 1.9e9))   # 2030
        manifest.add_assignment("999", "ann", 1, "blind", db, status="returned",
                                returned_on="2026-11-01")
        assert any("after it was returned" in f for f in self.audit(db, tmp_path))

    def test_route_camera_and_date_mismatches(self, db, tmp_path):
        kit(tmp_path / "kits", "999", "ann", pass1_source="seeded")
        manifest.add_assignment("999", "ann", 1, "blind", db, camera="8",
                                assigned_on="2026-11-s12")
        found = " | ".join(self.audit(db, tmp_path))
        assert "route blind" in found and "camera 8" in found and "not YYYY-MM-DD" in found

    def test_pass2_without_returned_pass1(self, db, tmp_path):
        kit(tmp_path / "kits", "999", "ann")
        kit(tmp_path / "kits", "999", "rev", pass_no=2)
        manifest.add_assignment("999", "ann", 1, "blind", db)
        manifest.add_assignment("999", "rev", 2, "blind", db)
        assert any("no returned pass 1" in f for f in self.audit(db, tmp_path))

    def test_gold_row_and_gold_files(self, db, tmp_path):
        kit(tmp_path / "kits", "999", "ann")
        p2 = kit(tmp_path / "kits", "999", "rev", pass_no=2)
        manifest.add_assignment("999", "ann", 1, "blind", db, status="returned",
                                returned_on="2099-01-01")
        manifest.add_assignment("999", "rev", 2, "blind", db, status="gold",
                                returned_on="2099-01-01")
        found = " | ".join(self.audit(db, tmp_path))
        assert "gold/999/999.gold.eaf" in found and "gold table has no row" in found
        gold = tmp_path / "gold" / "999" / "999.gold.eaf"
        gold.parent.mkdir(parents=True)
        gold.write_bytes(p2.read_bytes())
        with manifest.connect(db) as conn:
            conn.execute("INSERT INTO recordings (recording_id) VALUES ('999')")
        manifest.record_gold("999", path=db)
        assert self.audit(db, tmp_path) == []

    def test_gold_file_with_no_gold_row(self, db, tmp_path):
        gold = tmp_path / "gold" / "999" / "999.gold.eaf"
        gold.parent.mkdir(parents=True)
        gold.write_text("x")
        (tmp_path / "kits").mkdir()
        assert any("no row is gold" in f for f in self.audit(db, tmp_path))
