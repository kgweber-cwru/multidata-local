"""export_gold round-tripped through pympi's own writer -- builds a synthetic
.eaf in a tmp dir (no real recording needed) and reads it back, exactly the
"is this structurally sound" check described in tests/README.md. importorskips
pympi so the rest of the suite still runs in an env without it."""
import pytest

pympi = pytest.importorskip("pympi")

from multidata.elan import export_gold  # noqa: E402


def _build_eaf(path, annotations):
    """annotations: {tier: [(start_ms, end_ms, text), ...]}"""
    eaf = pympi.Elan.Eaf()
    for tier, spans in annotations.items():
        eaf.add_tier(tier)
        for start, end, text in spans:
            eaf.add_annotation(id_tier=tier, start=start, end=end, value=text)
    eaf.to_file(str(path))


class TestTierInclusion:
    def test_text_includes_only_speech_tiers(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {
            "LEARNER": [(0, 1000, "hello")],
            "PATIENT": [(1500, 2500, "hi doctor")],
            "ANNOUNCEMENT": [(3000, 4000, "paging doctor smith")],
            "OUTSIDE_ROOM": [(4500, 5000, "someone in the hallway")],
            "NOTES": [(0, 100, "noisy AC unit throughout")],
        })
        text, rttm_lines, _ = export_gold(eaf_path)
        assert text == "hello hi doctor"
        assert "paging" not in text
        assert "hallway" not in text
        assert "AC unit" not in text

    def test_rttm_includes_announcement_and_outside_room_but_not_notes(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {
            "LEARNER": [(0, 1000, "hello")],
            "ANNOUNCEMENT": [(3000, 4000, "paging doctor smith")],
            "OUTSIDE_ROOM": [(4500, 5000, "someone in the hallway")],
            "NOTES": [(0, 100, "noisy AC unit throughout")],
        })
        _, rttm_lines, _ = export_gold(eaf_path)
        labels = {line.split()[7] for line in rttm_lines}
        assert labels == {"LEARNER", "ANNOUNCEMENT", "OUTSIDE_ROOM"}

    def test_missing_optional_tier_is_not_an_error(self, tmp_path):
        # A case with no ANNOUNCEMENT/OUTSIDE_ROOM content at all.
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {"LEARNER": [(0, 1000, "hello")]})
        text, rttm_lines, _ = export_gold(eaf_path)
        assert text == "hello"
        assert len(rttm_lines) == 1


class TestChronologicalOrder:
    def test_text_interleaves_speakers_by_start_time(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {
            "LEARNER": [(0, 1000, "hello"), (3000, 4000, "how are you")],
            "PATIENT": [(1500, 2500, "hi doctor")],
        })
        text, _, _ = export_gold(eaf_path)
        # Word order must follow chronology across tiers, not tier-by-tier --
        # WER is a sequence alignment, not a bag of words.
        assert text == "hello hi doctor how are you"


class TestMergeAdjacent:
    def test_close_segments_merge_into_one_turn(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {
            "LEARNER": [(0, 1000, "hello"), (1100, 2000, "there")],  # 0.1s gap
        })
        _, rttm_lines, _ = export_gold(eaf_path, merge_gap=0.3)
        assert len(rttm_lines) == 1
        fields = rttm_lines[0].split()
        assert float(fields[3]) == 0.0
        assert float(fields[4]) == pytest.approx(2.0)

    def test_distant_segments_stay_separate(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {
            "LEARNER": [(0, 1000, "hello"), (2000, 3000, "there")],  # 1.0s gap
        })
        _, rttm_lines, _ = export_gold(eaf_path, merge_gap=0.3)
        assert len(rttm_lines) == 2

    def test_merge_gap_is_configurable(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {
            "LEARNER": [(0, 1000, "hello"), (1200, 2000, "there")],  # 0.2s gap
        })
        _, tight, _ = export_gold(eaf_path, merge_gap=0.1)
        _, loose, _ = export_gold(eaf_path, merge_gap=0.3)
        assert len(tight) == 2
        assert len(loose) == 1


class TestRedaction:
    def test_names_redacted_in_text(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {
            "LEARNER": [(0, 1000, "hi I'm Jamie")],
        })
        text, _, subs = export_gold(eaf_path, names={"learner": "Jamie"})
        assert "[LEARNER_NAME]" in text
        assert "Jamie" not in text
        assert len(subs) == 1

    def test_names_none_skips_redaction(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {"LEARNER": [(0, 1000, "hi I'm Jamie")]})
        text, _, subs = export_gold(eaf_path, names=None)
        assert "Jamie" in text
        assert subs == []


class TestFileWriting:
    def test_writes_txt_and_rttm(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {"LEARNER": [(0, 1000, "hello")]})
        out_txt = tmp_path / "out" / "case.gold.txt"
        out_rttm = tmp_path / "out" / "case.gold.rttm"
        export_gold(eaf_path, out_txt=out_txt, out_rttm=out_rttm)
        assert out_txt.read_text().strip() == "hello"
        assert "SPEAKER" in out_rttm.read_text()

    def test_no_paths_means_no_files(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {"LEARNER": [(0, 1000, "hello")]})
        export_gold(eaf_path)
        assert not (tmp_path / "case.gold.txt").exists()


class TestUri:
    def test_uri_defaults_to_eaf_stem(self, tmp_path):
        eaf_path = tmp_path / "132704.pass1.eaf"
        _build_eaf(eaf_path, {"LEARNER": [(0, 1000, "hello")]})
        _, rttm_lines, _ = export_gold(eaf_path)
        assert rttm_lines[0].split()[1] == "132704.pass1"

    def test_uri_override(self, tmp_path):
        eaf_path = tmp_path / "case.eaf"
        _build_eaf(eaf_path, {"LEARNER": [(0, 1000, "hello")]})
        _, rttm_lines, _ = export_gold(eaf_path, uri="132704")
        assert rttm_lines[0].split()[1] == "132704"
