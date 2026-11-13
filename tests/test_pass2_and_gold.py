"""The pass-1 -> pass-2 -> gold hand-offs: make_pass2_kit.py builds the
adjudication kit, eaf_to_gold.py --from-kit turns its result into gold, and
bench_status.py counts only scores made against the current gold."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "annotation"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from test_submit_checks import eaf  # noqa: E402

from multidata import manifest  # noqa: E402
import assignments  # noqa: E402
import make_pass2_kit  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """A manifest with recording 999 and ann's pass 1 out for work."""
    path = tmp_path / "m.sqlite"
    with manifest.connect(path) as conn:
        conn.execute("INSERT INTO recordings (recording_id, learner_name, sp_name) "
                     "VALUES ('999', 'Jamie', 'Alex')")
    manifest.add_assignment("999", "ann", 1, "blind", path, camera="7",
                            assigned_on="2026-10-01")
    return path


def pass1_kit(kits, recording="999", who="ann", text="hello there"):
    kit = kits / recording / who
    (kit / "media").mkdir(parents=True)
    (kit / "media" / "x.wav").write_bytes(b"wav")
    (kit / f"{recording}.pass1.eaf").write_text(eaf([("LEARNER", 0, 1000, text)]))
    (kit / f"{recording}.pass1.pfsx").write_text("prefs")
    (kit / "README.txt").write_text("blind pass 1")
    (kit / "kit.json").write_text(json.dumps({
        "recording_id": recording, "annotator": who, "eaf": f"{recording}.pass1.eaf",
        "span_start": None, "span_end": None}))
    return kit


def run_pass2(monkeypatch, kits, db, *argv):
    monkeypatch.setattr(make_pass2_kit, "KITS", kits)
    monkeypatch.setattr(sys, "argv", ["make_pass2_kit.py", "--manifest", str(db), *argv])
    make_pass2_kit.main()


class TestMakePass2Kit:
    def test_builds_kit_and_leaves_pass1_alone(self, tmp_path, monkeypatch, db):
        src = pass1_kit(tmp_path)
        before = (src / "999.pass1.eaf").read_bytes()
        run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        dst = tmp_path / "999" / "rev"
        assert (dst / "999.pass2.eaf").read_bytes() == before
        assert not (dst / "999.pass1.eaf").exists()
        assert not list(dst.glob("*.pfsx"))
        assert (dst / "media" / "x.wav").exists()
        kit = json.loads((dst / "kit.json").read_text())
        assert kit["annotator"] == "rev" and kit["pass1_annotator"] == "ann"
        assert kit["pass"] == 2 and kit["eaf"] == "999.pass2.eaf"
        assert "pass 2" in (dst / "README.txt").read_text()
        assert (src / "999.pass1.eaf").read_bytes() == before
        assert json.loads((src / "kit.json").read_text())["annotator"] == "ann"

    def test_same_person_refused(self, tmp_path, monkeypatch, db):
        pass1_kit(tmp_path)
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "ann")

    def test_existing_pass2_kit_refused(self, tmp_path, monkeypatch, db):
        pass1_kit(tmp_path)
        (tmp_path / "999" / "rev").mkdir()
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")

    def test_pass1_failing_checks_refused(self, tmp_path, monkeypatch, db):
        pass1_kit(tmp_path, text="")
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        assert not (tmp_path / "999" / "rev").exists()
        assert manifest.get_assignment("999", "ann", db)["status"] == "assigned"
        assert manifest.get_assignment("999", "rev", db) is None

    def test_rows_pass1_returned_and_pass2_assigned(self, tmp_path, monkeypatch, db):
        pass1_kit(tmp_path)
        run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        first = manifest.get_assignment("999", "ann", db)
        second = manifest.get_assignment("999", "rev", db)
        assert first["status"] == "returned" and first["returned_on"] == assignments.today()
        assert second["pass"] == 2 and second["route"] == "blind"
        assert second["status"] == "assigned" and second["assigned_on"] == assignments.today()

    def test_already_returned_pass1_keeps_its_date(self, tmp_path, monkeypatch, db):
        pass1_kit(tmp_path)
        manifest.update_assignment("999", "ann", db, status="returned", returned_on="2026-10-20")
        run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        assert manifest.get_assignment("999", "ann", db)["returned_on"] == "2026-10-20"

    def test_overwrite_rebuilds_from_updated_pass1(self, tmp_path, monkeypatch, db):
        src = pass1_kit(tmp_path)
        run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        dst = tmp_path / "999" / "rev"
        (dst / "stale.txt").write_text("from the old kit")
        manifest.update_assignment("999", "ann", db, returned_on="2026-01-01")
        manifest.update_assignment("999", "rev", db, assigned_on="2026-01-02")
        (src / "999.pass1.eaf").write_text(eaf([("LEARNER", 0, 1000, "hello there, fixed")]))
        run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev",
                  "--overwrite")
        assert (dst / "999.pass2.eaf").read_bytes() == (src / "999.pass1.eaf").read_bytes()
        assert not (dst / "stale.txt").exists()
        first = manifest.get_assignment("999", "ann", db)
        second = manifest.get_assignment("999", "rev", db)
        assert first["returned_on"] == assignments.today() and "2026-01-01" in first["notes"]
        assert second["assigned_on"] == "2026-01-02" and "rebuilt" in second["notes"]

    def test_existing_kit_needs_overwrite(self, tmp_path, monkeypatch, db):
        pass1_kit(tmp_path)
        run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")

    @pytest.mark.parametrize("status", ["returned", "gold"])
    def test_overwrite_refused_after_pass2_is_back(self, tmp_path, monkeypatch, db, status):
        pass1_kit(tmp_path)
        run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        manifest.update_assignment("999", "rev", db, status=status)
        before = (tmp_path / "999" / "rev" / "999.pass2.eaf").read_bytes()
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev",
                      "--overwrite")
        assert (tmp_path / "999" / "rev" / "999.pass2.eaf").read_bytes() == before

    @pytest.mark.parametrize("problem", ["no row", "abandoned"])
    def test_pass1_without_a_live_row_refused(self, tmp_path, monkeypatch, db, problem):
        pass1_kit(tmp_path)
        if problem == "no row":
            with manifest.connect(db) as conn:
                conn.execute("DELETE FROM assignments")
        else:
            manifest.update_assignment("999", "ann", db, status="abandoned")
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, db, "--recording", "999", "--from", "ann", "--to", "rev")
        assert not (tmp_path / "999" / "rev").exists()


class TestFromPass2Kit:
    @pytest.fixture
    def eaf_to_gold(self, tmp_path, monkeypatch):
        pytest.importorskip("pympi")
        import eaf_to_gold
        monkeypatch.setattr(eaf_to_gold, "KITS", tmp_path / "kits")
        monkeypatch.setattr(eaf_to_gold, "GOLD", tmp_path / "gold")
        return eaf_to_gold

    def _args(self, eaf_to_gold, kit_dir, db, **kw):
        import argparse
        base = dict(from_kit=str(kit_dir), replace_gold=False, eaf=None, recording=None,
                    kit=None, annotator="", gold_notes="", span_start=None, span_end=None,
                    manifest=str(db), pass1_frozen_at="")
        return argparse.Namespace(**(base | kw))

    def _pass2(self, tmp_path, monkeypatch, db):
        pass1_kit(tmp_path / "kits")
        run_pass2(monkeypatch, tmp_path / "kits", db,
                  "--recording", "999", "--from", "ann", "--to", "rev")
        return tmp_path / "kits" / "999" / "rev"

    def test_fills_args_and_copies_to_gold(self, eaf_to_gold, tmp_path, monkeypatch, db):
        kit = self._pass2(tmp_path, monkeypatch, db)
        args = self._args(eaf_to_gold, kit, db)
        eaf_to_gold.from_pass2_kit(args)
        gold = tmp_path / "gold" / "999" / "999.gold.eaf"
        assert gold.read_bytes() == (kit / "999.pass2.eaf").read_bytes()
        assert args.recording == "999" and args.eaf == str(gold)
        assert args.kit == str(tmp_path / "kits" / "999" / "ann")
        assert args.annotator == "ann" and args.gold_notes == "pass 2: rev"
        assert args.pass2_annotator == "rev"
        assert args.pass1_frozen_at == assignments.today()

    def test_marks_pass2_row_gold(self, eaf_to_gold, tmp_path, monkeypatch, db):
        self._pass2(tmp_path, monkeypatch, db)
        eaf_to_gold.mark_gold("999", "rev", str(db))
        row = manifest.get_assignment("999", "rev", db)
        assert row["status"] == "gold" and row["returned_on"] == assignments.today()

    def test_refuses_to_overwrite_different_gold(self, eaf_to_gold, tmp_path, monkeypatch, db):
        kit = self._pass2(tmp_path, monkeypatch, db)
        gold = tmp_path / "gold" / "999" / "999.gold.eaf"
        gold.parent.mkdir(parents=True)
        gold.write_text("an older adjudication")
        with pytest.raises(SystemExit):
            eaf_to_gold.from_pass2_kit(self._args(eaf_to_gold, kit, db))
        assert gold.read_text() == "an older adjudication"

    def test_name_gap_reported(self, eaf_to_gold, tmp_path):
        p = tmp_path / "x.eaf"
        p.write_text(eaf([("LEARNER", 0, 1000, "hi"), ("PRECEPTOR", 0, 1000, "hello")]))
        names = {"learner": "Jamie", "patient": "Alex", "preceptor": ""}
        assert eaf_to_gold.unredactable_tiers(p, names) == ["PRECEPTOR"]


class TestBenchStatusCurrent:
    def test_only_current_method_and_gold_count(self, tmp_path):
        pytest.importorskip("pympi")
        import bench_status
        import run_benchmark
        (tmp_path / "1.gold.txt").write_text("hello")
        sha = run_benchmark.sha256(tmp_path / "1.gold.txt")
        ok = {"recording_id": "1", "scoring": run_benchmark.SCORING, "gold_sha256": sha}
        assert bench_status.is_current(ok, tmp_path)
        assert not bench_status.is_current(ok | {"scoring": ""}, tmp_path)
        assert not bench_status.is_current(ok | {"gold_sha256": "old"}, tmp_path)
        assert not bench_status.is_current(ok | {"recording_id": "2"}, tmp_path)
