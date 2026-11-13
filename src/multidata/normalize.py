"""Text normalization for scoring — the L0/L1/L2 profiles
(docs/transcription_standards.md §7, docs/asr_provider_spec.md §6).

Stdlib-only and pure: every function here is text-in, text-out, with no I/O
and no model. That's deliberate — this module has the largest blast radius in
the benchmarking wing. A bug in filler stripping silently corrupts every L1
number in every future run, and nothing about the output would look wrong.
Tests in `tests/test_normalize.py` are the actual safety net; read them
alongside this file.

Three levels, each a deterministic transform of the one above:

- **L0 verbatim** — every word spoken, with the always-applied scoring
  transforms (casing, punctuation, whitespace, bracket stripping) and the
  annotators' markup taken off the words: "so:" -> "so", "I-" -> "I". An
  engine is never penalized for a mark no engine writes. Fillers,
  backchannels and cut-off words all survive.
- **L1 filler-neutral** — L0 minus filled pauses and cut-off words (the
  disfluency, not the message). Backchannels still survive — they carry
  meaning ("uh-uh" is a clinical *no*), fillers don't.
- **L2 clean** — L1 with adjacent repeated tokens collapsed. A deliverable
  for readability, never a scoring profile.

Two transforms are deliberately NOT implemented here yet: **number
canonicalization** and **name redaction is a separate module** (`redact.py`,
since it needs recording-specific names, not a fixed rule). See the module-level
`NUMBER_CANONICALIZATION_TODO` note below for why numbers are deferred rather
than guessed at.
"""
import re

# The only three filled pauses in the standard (transcription_standards.md
# §6). `er`/`erm` are deliberately excluded — British-convention variants,
# and `uh` vs `er` is a coin flip for American speakers, so keeping both would
# only add inter-annotator noise, not signal.
FILLERS = frozenset({"uh", "um", "hmm"})

# Backchannels carry meaning and must never be stripped at any level.
# `uh-huh` (yes) and `uh-uh` (no) are one letter apart with opposite
# meanings — this set exists specifically so a filler-stripping bug can never
# silently delete a patient's answer.
#
# Written in *post-`scoring_base`* form: matching happens after punctuation
# stripping (below), so "huh" not "huh?" — annotators still write "huh?" per
# transcription_standards.md §6 (natural orthography; the scorer neutralizes
# punctuation the same way for every word, backchannels included).
# `mm` is the continuer ("go on, I'm listening"); a thinking hum is `hmm`,
# a filler. The spelling alone decides, so no human judgment at scoring.
BACKCHANNELS = frozenset({"mm-hmm", "uh-huh", "uh-uh", "mm-mm", "huh", "mm"})

# A word token ending in ":" is a stretched sound (prolongation). The colon is
# markup: stripped at every level, keeping the word ("so:" -> "so").
_PROLONGATION = re.compile(r"^(\S+):$")

# A word token ending in "-" is a cut-off or abandoned word ("par-", "I-").
# At L0 the hyphen is markup and goes, keeping what was said ("I-" -> "i");
# at L1 the whole token goes, like a filler.
_FRAGMENT = re.compile(r"^(\S+)-$")

# [unintelligible], [unintelligible: word?], [laugh], [cough], etc. — every
# annotator bracket tag in the standard. Stripped at every scoring level: an
# uncertain span shouldn't count for or against any engine, and a provider
# that natively emits audio-event tags (ElevenLabs) shouldn't be penalized
# for supplying more information than the others.
_BRACKETS = re.compile(r"\[[^\]]*\]")

# Generic sentence punctuation, stripped for scoring per the standard
# ("casing & punctuation ... normalized away before WER" — annotate
# naturally, the scorer neutralizes it). Deliberately excludes ":" and "-",
# which are markup handled by _PROLONGATION/_FRAGMENT before this runs, not
# noise to be stripped blindly.
_PUNCTUATION = re.compile(r"[.,!?;\"'()]")

_WHITESPACE = re.compile(r"\s+")

# A token with no letter or digit left after the above -- a free-standing
# dash used to mark a pause or interruption ("I - I think"), an ellipsis, an
# em dash. Markup, not a word: dropped so it can't count as one. Attached
# dashes ("wh-", "mm-hmm") are untouched.
_NO_WORD = re.compile(r"^[^\w]+$")


def _tokenize(text):
    return text.split()


def strip_brackets(text):
    """Remove every `[...]` span. Applied identically to reference and
    hypothesis (asr_provider_spec.md §6) — this is what makes an
    `[unintelligible]` gold span and a provider's native `[laughter]` tag both
    scoring-neutral instead of counting as insertions/deletions."""
    return _BRACKETS.sub("", text)


def _normalize_whitespace(text):
    return _WHITESPACE.sub(" ", text).strip()


# Spelling variants of the same sound, written differently by different
# annotators and engines ("mhm", "mmm-hmm"), mapped to the standard's form
# on both sides, so a spelling choice never counts as a mishearing. Only
# unambiguous shapes: "uhuh" could be uh-huh (yes) or uh-uh (no), so it is
# left alone rather than guessed.
_VARIANTS = (
    (re.compile(r"^m+-?h+m+$"), "mm-hmm"),      # mhm, mmhmm, mm-hm, mmm-hmm
    (re.compile(r"^u+h+-?h+u+h+$"), "uh-huh"),  # uhhuh, uhh-huh
    (re.compile(r"^u+h+-u+h+$"), "uh-uh"),      # uhh-uh (the hyphen makes it unambiguous)
    (re.compile(r"^m+-m+$"), "mm-mm"),          # mmm-mm
    (re.compile(r"^h+m+$"), "hmm"),             # hm, hmmm
    (re.compile(r"^m{2,}$"), "mm"),             # mmm, mmmm
    (re.compile(r"^ok$"), "okay"),              # OK, O.K. (dots already gone)
)


def _canonical(tok):
    # Keep a trailing markup mark ("ok-", "mhm:") so _unmark still sees it.
    mark = tok[-1] if len(tok) > 1 and tok[-1] in "-:" else ""
    word = tok[:-1] if mark else tok
    for pattern, form in _VARIANTS:
        if pattern.match(word):
            return form + mark
    return tok


def _marked_tokens(text):
    """Tokens after the shared transforms (brackets, casing, punctuation,
    free-standing marks) but with the annotators' prolongation and cut-off
    markup still attached, so L1 can still tell which tokens were cut off."""
    text = strip_brackets(text)
    text = text.lower()
    text = _PUNCTUATION.sub("", text)
    return [_canonical(t) for t in _tokenize(text) if not _NO_WORD.match(t)]


def _unmark(tok):
    """"so:" -> "so", "i-" -> "i"; anything else unchanged."""
    m = _PROLONGATION.match(tok) or _FRAGMENT.match(tok)
    return m.group(1) if m else tok


def scoring_base(text):
    """L0's scoring transform: lowercase, strip generic punctuation, strip
    bracketed spans, drop free-standing marks, take prolongation and cut-off
    marks off their words, collapse whitespace. Backchannels, fillers and
    cut-off words all survive — this is the "verbatim" profile, only
    normalized for the things WER always ignores (casing/punctuation), the
    annotators' markup (no engine writes "so:" or "I-"), and what can never
    be scored fairly (uncertain/bracketed spans).

    Name redaction and number canonicalization are separate steps, applied
    before this if at all (see module docstring and `redact.py`) — order
    matters for name redaction (it needs case-sensitive names to fuzzy-match
    against), so it is never bundled into this function.
    """
    return " ".join(_unmark(t) for t in _marked_tokens(text))


def to_filler_neutral(text):
    """L1: L0 minus filled pauses and cut-off words — the disfluency, not the
    message (transcription_standards.md §7). Backchannels always survive.

    Call it on the annotated text, not on `scoring_base` output: L0 has
    already taken the hyphen off "i-", so a cut-off word is no longer
    recognizable there.
    """
    out_tokens = []
    for tok in _marked_tokens(text):
        if tok in BACKCHANNELS:
            out_tokens.append(tok)
            continue
        if tok in FILLERS or _FRAGMENT.match(tok):
            continue
        out_tokens.append(_unmark(tok))
    return " ".join(out_tokens)


def to_clean(text):
    """L2: `to_filler_neutral` plus adjacent-repetition collapse. A deliverable for readability — **never a scoring profile**
    (transcription_standards.md §7). Repetition collapse is generic (any
    adjacent identical tokens, not filler-specific), matching the standard's
    "just type them, L2 collapses them automatically" rule; a genuinely
    repeated backchannel would also collapse, which is correct — it's the same
    mechanism, not a special case.
    """
    collapsed = []
    for tok in _tokenize(to_filler_neutral(text)):
        if collapsed and collapsed[-1] == tok:
            continue
        collapsed.append(tok)
    return " ".join(collapsed)


def normalize(text, level="l0"):
    """Dispatch to the named profile: "l0" (default), "l1", or "l2".

    This is the one entry point scoring code should call rather than reaching
    for `scoring_base`/`to_filler_neutral`/`to_clean` directly, so a new
    caller can't accidentally pick the wrong level by typo — invalid levels
    raise rather than silently falling back to L0.
    """
    level = level.lower()
    if level == "l0":
        return scoring_base(text)
    if level == "l1":
        return to_filler_neutral(text)
    if level == "l2":
        return to_clean(text)
    raise ValueError(f"Unknown normalization level {level!r}; pick one of l0/l1/l2")


# NUMBER_CANONICALIZATION_TODO: transcription_standards.md §6 has annotators
# write numbers as spoken ("one twenty over eighty") specifically so a
# provider's own number *formatting* doesn't get benchmarked by accident.
# Comparing that fairly against a provider's digit-form output ("120/80")
# needs a real spoken-number <-> digit canonicalizer, and getting that right
# blind — before any gold transcript with real numeric content exists to
# design against — risks building confident-looking rules for formats nobody
# actually uses here (units, ranges, "over" as a separator, decimals). Left
# unimplemented on purpose rather than shipped half-right; revisit once
# Phase 5 (docs/asr_provider_implementation_plan.md) produces gold with
# numbers in it.
