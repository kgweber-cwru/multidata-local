"""The critical-path tests named explicitly in
docs/asr_provider_implementation_plan.md Phase 1.3: this module has the
largest blast radius in the benchmarking wing, because a bug here silently
corrupts every L1 number in every future run without looking wrong."""
import pytest

from multidata import normalize


class TestBackchannelsSurvive:
    """The one that matters most (docs/annotator_guide.md): 'uh-uh' is a
    clinical no. If this class ever fails, L1 is deleting patient answers."""

    @pytest.mark.parametrize("token", sorted(normalize.BACKCHANNELS))
    def test_backchannel_survives_l1(self, token):
        text = f"well {token} okay"
        assert token in normalize.to_filler_neutral(text).split()

    @pytest.mark.parametrize("token", sorted(normalize.BACKCHANNELS))
    def test_backchannel_survives_l2(self, token):
        text = f"well {token} okay"
        assert token in normalize.to_clean(text).split()

    def test_uh_uh_and_uh_huh_are_not_confused(self):
        no_answer = normalize.to_filler_neutral("any chest pain uh-uh")
        yes_answer = normalize.to_filler_neutral("any chest pain uh-huh")
        assert "uh-uh" in no_answer.split()
        assert "uh-huh" in yes_answer.split()
        assert no_answer != yes_answer


class TestFillersRemoved:
    @pytest.mark.parametrize("filler", sorted(normalize.FILLERS))
    def test_filler_removed_at_l1(self, filler):
        text = f"well {filler} so the patient"
        assert filler not in normalize.to_filler_neutral(text).split()

    @pytest.mark.parametrize("filler", sorted(normalize.FILLERS))
    def test_filler_survives_at_l0(self, filler):
        text = f"well {filler} so the patient"
        assert filler in normalize.scoring_base(text).split()

    def test_er_is_not_treated_as_a_filler(self):
        # Deliberately excluded (transcription_standards.md §6) -- `er` is
        # a real English word/false-start marker here, not in the closed set.
        assert "er" not in normalize.FILLERS


class TestBrackets:
    def test_unintelligible_span_removed(self):
        assert normalize.scoring_base("she said [unintelligible] yesterday") == \
            "she said yesterday"

    def test_guessed_span_removed(self):
        assert normalize.scoring_base("her [unintelligible: pressure?] was high") == \
            "her was high"

    def test_event_tag_removed(self):
        assert normalize.scoring_base("that is funny [laugh] right") == "that is funny right"

    def test_brackets_gone_at_every_level(self):
        text = "so [cough] the patient [unintelligible] said uh okay"
        for level in ("l0", "l1", "l2"):
            assert "[" not in normalize.normalize(text, level)


class TestProlongation:
    def test_colon_stripped_word_kept(self):
        assert normalize.to_filler_neutral("so: she was worried") == "so she was worried"

    def test_prolongation_removed_but_word_survives_not_deleted(self):
        out = normalize.to_filler_neutral("the: patient came in")
        assert "the" in out.split()
        assert "the:" not in out.split()


class TestFragments:
    def test_fragment_survives_l1(self):
        # L1 = L0 minus fillers/prolongation/brackets only -- fragments are
        # still real (if incomplete) disfluency content at this level.
        out = normalize.to_filler_neutral("par- particular symptom")
        assert "par-" in out.split()

    def test_fragment_removed_only_at_l2(self):
        out = normalize.to_clean("par- particular symptom")
        assert "par-" not in out.split()
        assert "particular" in out.split()


class TestRepetitionCollapse:
    def test_l2_collapses_adjacent_repeats(self):
        assert normalize.to_clean("I I I went to the store") == "i went to the store"

    def test_l1_does_not_collapse_repeats(self):
        # Repetition collapse is an L2-only, readability transform
        # (transcription_standards.md §7) -- L1 keeps them as real content.
        out = normalize.to_filler_neutral("I I I went")
        assert out.split().count("i") == 3

    def test_l2_collapse_is_generic_not_filler_specific(self):
        # Two adjacent backchannels collapsing is the same generic mechanism,
        # not a special case -- distinct from a backchannel being *deleted*.
        assert normalize.to_clean("uh-huh uh-huh yes") == "uh-huh yes"

    def test_fragment_removal_then_repetition_collapse_compose(self):
        # "I went- I drove" -> drop "went-" -> "i i drove" -> collapse -> "i drove"
        assert normalize.to_clean("I went- I drove") == "i drove"


class TestCasingAndPunctuation:
    def test_lowercased(self):
        assert normalize.scoring_base("The Patient Said") == "the patient said"

    def test_generic_punctuation_stripped(self):
        assert normalize.scoring_base("Hello, doctor! How are you?") == \
            "hello doctor how are you"

    def test_whitespace_collapsed(self):
        assert normalize.scoring_base("so   uh   yeah") == "so uh yeah"


class TestDispatch:
    def test_l0_l1_l2_dispatch(self):
        text = "well, uh, so: par- particular I I I went"
        assert normalize.normalize(text, "l0") == normalize.scoring_base(text)
        assert normalize.normalize(text, "l1") == normalize.to_filler_neutral(text)
        assert normalize.normalize(text, "l2") == normalize.to_clean(text)

    def test_dispatch_is_case_insensitive(self):
        assert normalize.normalize("uh hi", "L1") == normalize.normalize("uh hi", "l1")

    def test_unknown_level_raises(self):
        with pytest.raises(ValueError):
            normalize.normalize("hi", "l99")


class TestSharedTransformIdenticalBothSides:
    """The rule from asr_provider_spec.md §6: transforms apply identically
    to reference and hypothesis, or the comparison isn't fair."""

    def test_ref_and_hyp_with_equivalent_content_match_at_l1(self):
        ref = "Well, UH, the patient's [unintelligible] chest hurt."
        hyp = "well the patient's chest hurt"
        assert normalize.to_filler_neutral(ref) == normalize.to_filler_neutral(hyp)
