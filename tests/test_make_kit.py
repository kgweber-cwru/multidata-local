"""Kit generation -- above all, that a kit's .eaf points at the canonical path.

That one property is what stops ELAN showing a "locate media" dialog on a
machine that isn't the one the file was made on -- see annotation/README.md
and make_kit.py's own docstring for the current state of what uses this
(the cloud environment this was originally built for was torn out 2026-09-23,
see docs/current_status.md). If these tests fail, annotators are back to
hand-locating media.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "annotation"))

pytest.importorskip("pympi", reason="kit generation writes .eaf via pympi")

import make_kit  # noqa: E402

TIERS = ["LEARNER", "PATIENT", "PRECEPTOR", "ANNOUNCEMENT", "OUTSIDE_ROOM", "NOTES"]


def descriptors(eaf_path):
    root = ET.parse(eaf_path).getroot()
    return [(d.get("MEDIA_URL"), d.get("RELATIVE_MEDIA_URL"), d.get("MIME_TYPE"))
            for d in root.iter("MEDIA_DESCRIPTOR")]


class TestMediaLinking:
    def test_absolute_urls_use_the_canonical_path_not_this_machine(self, tmp_path):
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "261473")
        urls = [u for u, _, _ in descriptors(out)]
        assert urls == [
            "file:///srv/multidata/case/261473/media/261473.mp4",
            "file:///srv/multidata/case/261473/media/261473.wav",
        ]
        assert not any(str(tmp_path) in u for u in urls)

    def test_relative_urls_resolve_from_the_eaf_beside_it(self, tmp_path):
        # Both attributes have to be right: ELAN falls back to the relative one,
        # and today's hand-made gold files have a relative URL that only
        # resolves by luck of where they were opened from.
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "261473")
        assert [r for _, r, _ in descriptors(out)] == [
            "media/261473.mp4", "media/261473.wav"]

    def test_both_media_files_are_linked_with_the_right_types(self, tmp_path):
        # The guide asks for both: video shows who is speaking, the wav gives a
        # real waveform rather than a video-derived guess.
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "999")
        assert [m for _, _, m in descriptors(out)] == ["video/mp4", "audio/x-wav"]

    def test_case_id_appears_in_every_path(self, tmp_path):
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "123456")
        for url, rel, _ in descriptors(out):
            assert "123456" in url and "123456" in rel


class TestTiers:
    def test_the_six_template_tiers_come_through(self, tmp_path):
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "999")
        root = ET.parse(out).getroot()
        assert [t.get("TIER_ID") for t in root.iter("TIER")] == TIERS

    def test_no_spurious_default_tier(self, tmp_path):
        # A leftover `default` tier is a known hazard -- an older duplicate
        # template had one, and it is invisible until it corrupts agreement.
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "999")
        root = ET.parse(out).getroot()
        assert "default" not in [t.get("TIER_ID") for t in root.iter("TIER")]

    def test_refuses_a_template_with_the_wrong_tiers(self, tmp_path, monkeypatch):
        # Changing the tier set is a standards change, not a file edit. If the
        # template drifts, kit generation should stop rather than quietly ship
        # a different annotation scheme.
        bad = tmp_path / "bad.etf"
        bad.write_text((ROOT / "elan" / "template.etf").read_text()
                       .replace('TIER_ID="NOTES"', 'TIER_ID="SCRATCH"'))
        monkeypatch.setattr(make_kit, "TEMPLATE", bad)
        with pytest.raises(SystemExit, match="standards change"):
            make_kit.write_eaf(tmp_path / "k.eaf", "999")


class TestExcerptWindow:
    def test_no_span_means_no_annotations_at_all(self, tmp_path):
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "999")
        root = ET.parse(out).getroot()
        assert list(root.iter("ALIGNABLE_ANNOTATION")) == []

    def test_span_is_marked_on_the_notes_tier(self, tmp_path):
        # NOTES because it is excluded from both gold.txt and gold.rttm, so a
        # marker there cannot reach a published reference.
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "999", span=(120.0, 420.0))
        root = ET.parse(out).getroot()
        notes = [t for t in root.iter("TIER") if t.get("TIER_ID") == "NOTES"][0]
        values = [v.text for v in notes.iter("ANNOTATION_VALUE")]
        assert values == ["ANNOTATE ONLY THIS WINDOW"]

    def test_span_seconds_become_milliseconds(self, tmp_path):
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "999", span=(120.0, 420.0))
        root = ET.parse(out).getroot()
        times = sorted(int(s.get("TIME_VALUE")) for s in root.iter("TIME_SLOT"))
        assert times == [120000, 420000]

    def test_no_speaker_tier_is_pre_populated(self, tmp_path):
        # Pass 1 is blind: nothing but the window marker may be in the file.
        out = tmp_path / "k.eaf"
        make_kit.write_eaf(out, "999", span=(0.0, 60.0))
        root = ET.parse(out).getroot()
        for tier in root.iter("TIER"):
            if tier.get("TIER_ID") != "NOTES":
                assert list(tier.iter("ALIGNABLE_ANNOTATION")) == []


class TestResolve:
    def test_absolute_paths_are_left_alone(self):
        assert make_kit.resolve("/tmp/x.wav") == Path("/tmp/x.wav")

    def test_relative_paths_are_anchored_to_the_repo(self):
        # The manifest mixes the two: videos.filepath is repo-relative,
        # videos.audio_path is absolute.
        assert make_kit.resolve("data/raw/x.mp4") == ROOT / "data/raw/x.mp4"
