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

import make_pass2_kit  # noqa: E402


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


def run_pass2(monkeypatch, kits, *argv):
    monkeypatch.setattr(make_pass2_kit, "KITS", kits)
    monkeypatch.setattr(sys, "argv", ["make_pass2_kit.py", *argv])
    make_pass2_kit.main()


class TestMakePass2Kit:
    def test_builds_kit_and_leaves_pass1_alone(self, tmp_path, monkeypatch):
        src = pass1_kit(tmp_path)
        before = (src / "999.pass1.eaf").read_bytes()
        run_pass2(monkeypatch, tmp_path, "--recording", "999", "--from", "ann", "--to", "rev")
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

    def test_same_person_refused(self, tmp_path, monkeypatch):
        pass1_kit(tmp_path)
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, "--recording", "999", "--from", "ann", "--to", "ann")

    def test_existing_pass2_kit_refused(self, tmp_path, monkeypatch):
        pass1_kit(tmp_path)
        (tmp_path / "999" / "rev").mkdir()
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, "--recording", "999", "--from", "ann", "--to", "rev")

    def test_pass1_failing_checks_refused(self, tmp_path, monkeypatch):
        pass1_kit(tmp_path, text="")
        with pytest.raises(SystemExit):
            run_pass2(monkeypatch, tmp_path, "--recording", "999", "--from", "ann", "--to", "rev")
        assert not (tmp_path / "999" / "rev").exists()


class TestFromPass2Kit:
    @pytest.fixture
    def eaf_to_gold(self, tmp_path, monkeypatch):
        pytest.importorskip("pympi")
        import eaf_to_gold
        monkeypatch.setattr(eaf_to_gold, "KITS", tmp_path / "kits")
        monkeypatch.setattr(eaf_to_gold, "GOLD", tmp_path / "gold")
        return eaf_to_gold

    def _args(self, eaf_to_gold, kit_dir, **kw):
        import argparse
        base = dict(from_kit=str(kit_dir), replace_gold=False, eaf=None, recording=None,
                    kit=None, annotator="", gold_notes="", span_start=None, span_end=None)
        return argparse.Namespace(**(base | kw))

    def _pass2(self, tmp_path, monkeypatch):
        pass1_kit(tmp_path / "kits")
        run_pass2(monkeypatch, tmp_path / "kits", "--recording", "999", "--from", "ann", "--to", "rev")
        return tmp_path / "kits" / "999" / "rev"

    def test_fills_args_and_copies_to_gold(self, eaf_to_gold, tmp_path, monkeypatch):
        kit = self._pass2(tmp_path, monkeypatch)
        args = self._args(eaf_to_gold, kit)
        eaf_to_gold.from_pass2_kit(args)
        gold = tmp_path / "gold" / "999" / "999.gold.eaf"
        assert gold.read_bytes() == (kit / "999.pass2.eaf").read_bytes()
        assert args.recording == "999" and args.eaf == str(gold)
        assert args.kit == str(tmp_path / "kits" / "999" / "ann")
        assert args.annotator == "ann" and args.gold_notes == "pass 2: rev"

    def test_refuses_to_overwrite_different_gold(self, eaf_to_gold, tmp_path, monkeypatch):
        kit = self._pass2(tmp_path, monkeypatch)
        gold = tmp_path / "gold" / "999" / "999.gold.eaf"
        gold.parent.mkdir(parents=True)
        gold.write_text("an older adjudication")
        with pytest.raises(SystemExit):
            eaf_to_gold.from_pass2_kit(self._args(eaf_to_gold, kit))
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
