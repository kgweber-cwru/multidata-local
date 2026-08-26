from multidata import normalize
from multidata.redact import redact_names


class TestExactMatch:
    def test_case_insensitive_exact_match(self):
        text = "Hi I'm Jamie, third-year"
        redacted, subs = redact_names(text, {"learner": "Jamie Smith"})
        assert "[LEARNER_NAME]" in redacted
        assert "Jamie" not in redacted
        assert subs == [{"role": "learner", "matched": "Jamie", "tag": "[LEARNER_NAME]"}]

    def test_multiword_name_collapses_to_one_tag(self):
        text = "This is Jamie Smith speaking"
        redacted, subs = redact_names(text, {"learner": "Jamie Smith"})
        assert redacted == "This is [LEARNER_NAME] speaking"
        assert len(subs) == 1
        assert subs[0]["matched"] == "Jamie Smith"

    def test_all_three_roles(self):
        text = "Jamie asked Alex about her history while Dr. Lee observed"
        names = {"learner": "Jamie", "patient": "Alex", "preceptor": "Lee"}
        redacted, subs = redact_names(text, names)
        assert "[LEARNER_NAME]" in redacted
        assert "[PATIENT_NAME]" in redacted
        assert "[PRECEPTOR_NAME]" in redacted
        assert len(subs) == 3


class TestFuzzyMatch:
    def test_common_misspelling_caught(self):
        # A provider hearing "Jamie" and writing "Jamey" -- the documented
        # limitation this exists to partially cover (asr_provider_spec.md §9).
        redacted, subs = redact_names("hi I'm Jamey", {"learner": "Jamie"})
        assert "[LEARNER_NAME]" in redacted
        assert subs[0]["role"] == "learner"

    def test_dissimilar_word_not_redacted(self):
        redacted, subs = redact_names("the room was quiet", {"learner": "Jamie"})
        assert redacted == "the room was quiet"
        assert subs == []

    def test_short_words_do_not_fuzzy_match(self):
        # "Al" as a name part is too short to fuzzy-match safely -- would
        # otherwise catch half the short words in English.
        redacted, subs = redact_names("I was in a bad way", {"learner": "Al"})
        assert redacted == "I was in a bad way"
        assert subs == []


class TestFalsyNamesSkipped:
    def test_empty_name_skips_role(self):
        redacted, subs = redact_names("no preceptor here today", {"preceptor": ""})
        assert redacted == "no preceptor here today"
        assert subs == []

    def test_none_name_skips_role(self):
        redacted, subs = redact_names("fine", {"preceptor": None})
        assert redacted == "fine"
        assert subs == []


class TestBracketTagIntegratesWithNormalize:
    """The whole point of using [ROLE_NAME] bracket syntax: it passes
    straight through the existing bracket-stripping rule, so a redacted name
    is scoring-neutral for free."""

    def test_redacted_tag_is_stripped_by_scoring_base(self):
        redacted, _ = redact_names("hi I'm Jamie", {"learner": "Jamie"})
        assert normalize.scoring_base(redacted) == "hi im"

    def test_ref_and_hyp_agree_after_redaction_and_l0(self):
        ref = "the learner, Jamie, asked about her history"
        hyp = "the learner jamie asked about her history"
        names = {"learner": "Jamie"}
        ref_r, _ = redact_names(ref, names)
        hyp_r, _ = redact_names(hyp, names)
        assert normalize.scoring_base(ref_r) == normalize.scoring_base(hyp_r)
