"""The two checks that run before an annotator's pass is submitted.

Stdlib only -- `submit_checks.py` deliberately has no dependencies so it can
run on the annotator VM without anything installed, and these tests hold that
line by building .eaf XML by hand rather than through pympi.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "annotation"))

import submit_checks  # noqa: E402


def eaf(annotations):
    """Minimal .eaf XML. `annotations` is [(tier, start_ms, end_ms, text)]."""
    slots, tiers, n = [], {}, 0
    for tier, start, end, text in annotations:
        n += 1
        s1, s2 = f"ts{n}a", f"ts{n}b"
        slots += [f'<TIME_SLOT TIME_SLOT_ID="{s1}" TIME_VALUE="{start}"/>',
                  f'<TIME_SLOT TIME_SLOT_ID="{s2}" TIME_VALUE="{end}"/>']
        body = f"<ANNOTATION_VALUE>{text}</ANNOTATION_VALUE>" if text is not None else ""
        tiers.setdefault(tier, []).append(
            f'<ANNOTATION><ALIGNABLE_ANNOTATION ANNOTATION_ID="a{n}" '
            f'TIME_SLOT_REF1="{s1}" TIME_SLOT_REF2="{s2}">{body}'
            f'</ALIGNABLE_ANNOTATION></ANNOTATION>')
    tier_xml = "".join(
        f'<TIER TIER_ID="{t}">{"".join(a)}</TIER>' for t, a in tiers.items())
    return ('<?xml version="1.0" encoding="UTF-8"?><ANNOTATION_DOCUMENT>'
            f'<TIME_ORDER>{"".join(slots)}</TIME_ORDER>{tier_xml}'
            '</ANNOTATION_DOCUMENT>')


def written(tmp_path, annotations):
    path = tmp_path / "t.eaf"
    path.write_text(eaf(annotations))
    return submit_checks.read_annotations(path)


class TestReadAnnotations:
    def test_reads_tier_times_and_text(self, tmp_path):
        got = written(tmp_path, [("LEARNER", 1000, 2500, "hello there")])
        assert got == [("LEARNER", 1000, 2500, "hello there")]

    def test_strips_surrounding_whitespace(self, tmp_path):
        got = written(tmp_path, [("LEARNER", 0, 100, "  um  ")])
        assert got[0][3] == "um"

    def test_missing_annotation_value_reads_as_empty(self, tmp_path):
        # ELAN writes a segment with no text as an ALIGNABLE_ANNOTATION with no
        # ANNOTATION_VALUE child at all, not as an empty one.
        got = written(tmp_path, [("LEARNER", 0, 100, None)])
        assert got[0][3] == ""


class TestEmptySegmentsAreRefused:
    def test_no_problems_when_every_segment_has_text(self):
        problems, warnings = submit_checks.check([
            ("LEARNER", 0, 1000, "so what brings you in"),
            ("PATIENT", 1000, 2000, "uh my knee"),
        ])
        assert problems == [] and warnings == []

    def test_empty_segment_is_a_problem(self):
        problems, _ = submit_checks.check([("LEARNER", 410460, 410604, "")])
        assert len(problems) == 1
        assert "LEARNER" in problems[0]

    def test_problem_names_the_time_so_it_can_be_found(self):
        problems, _ = submit_checks.check([("PATIENT", 65000, 66500, "")])
        assert "1:05.000" in problems[0]

    def test_every_empty_segment_is_reported(self):
        problems, _ = submit_checks.check([
            ("LEARNER", 0, 100, ""),
            ("LEARNER", 200, 300, "words"),
            ("PATIENT", 400, 500, ""),
        ])
        assert len(problems) == 2


class TestExcerptWindowOnlyWarns:
    span = (120.0, 420.0)

    def test_inside_the_window_is_silent(self):
        problems, warnings = submit_checks.check(
            [("LEARNER", 130000, 140000, "inside")], span=self.span)
        assert problems == [] and warnings == []

    def test_outside_the_window_warns_but_is_not_a_problem(self):
        # Deliberate: the window is guidance, and .eaf-level excerpt slicing is
        # unbuilt, so refusing here would enforce a boundary the pipeline
        # itself doesn't.
        problems, warnings = submit_checks.check(
            [("LEARNER", 600000, 610000, "after the window")], span=self.span)
        assert problems == []
        assert len(warnings) == 1

    def test_partial_overlap_counts_as_inside(self):
        # An utterance straddling the boundary is the annotator doing the right
        # thing with a segment that happens to cross it.
        _, warnings = submit_checks.check(
            [("LEARNER", 119000, 121000, "straddles the start")], span=self.span)
        assert warnings == []

    def test_notes_tier_is_exempt(self):
        # A kit marks the window itself as a NOTES annotation, so flagging
        # NOTES would mean every excerpt kit warned about its own marker.
        _, warnings = submit_checks.check(
            [("NOTES", 120000, 420000, "ANNOTATE ONLY THIS WINDOW")],
            span=self.span)
        assert warnings == []

    def test_no_span_means_no_window_check(self):
        _, warnings = submit_checks.check(
            [("LEARNER", 999000, 999500, "anywhere")], span=None)
        assert warnings == []


class TestSummarise:
    def test_counts_per_tier(self):
        text = submit_checks.summarise([
            ("LEARNER", 0, 1, "a"), ("LEARNER", 1, 2, "b"),
            ("PATIENT", 2, 3, "c"),
        ])
        assert "LEARNER 2" in text and "PATIENT 1" in text

    def test_says_so_when_there_is_nothing(self):
        # An .eaf with no annotations passes both checks, so the summary line is
        # the only thing that makes that visible. Worth keeping honest.
        assert submit_checks.summarise([]) == "no annotations at all"
