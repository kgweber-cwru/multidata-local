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

**Numbers** are canonicalized on both sides at every level
(transcription_standards.md §6): gold writes them as spoken ("one twenty over
seventy-six"), engines write digits ("120/76"), and both become digit tokens
("120 over 76") so a vendor's number *formatting* is never scored. Digits are
the canonical form because spoken -> digits is deterministic and the reverse
is not ("120" may have been "one twenty" or "one hundred twenty"). The rules:

- a run of number words reads as standard English ("one hundred and twenty",
  "a hundred", "seventy-six"); a new number starts wherever the grammar
  can't continue, so "nine three seven" is 9 3 7 and "ninety-nine
  ninety-nine" is 99 99;
- the colloquial pair "one twenty", "three thirty", "nineteen eighty-four"
  (a number from one to nineteen, then a teen or tens word) reads as 120,
  330 and 1984, as it does in blood pressures, times and years;
- written forms are taken apart to match: "120/80" -> 120 over 80,
  "98.6" -> 98 point 6, "1,000" -> 1000, "3:30" -> 330, "10-15" -> 10 to 15,
  "50%" -> 50 percent, "M4" -> m 4;
- ordinals become "21st", "4th", except "second" (also a unit of time), which
  stays a word on both sides: "2nd" -> second;
- a number word carrying markup is converted alone, keeping the mark for
  the levels that use it: a cut-off "two-" -> "2-", a stretched "two:" ->
  "2:".

Name redaction is a separate module (`redact.py`, since it needs
recording-specific names, not a fixed rule).
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


# --- numbers -----------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine".split())}
_TEENS = {w: i + 10 for i, w in enumerate(
    "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}
_ORDINALS = {
    "first": 1, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
    "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60,
    "seventieth": 70, "eightieth": 80, "ninetieth": 90, "hundredth": 100,
}
_NUMBER_WORDS = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_SCALES) | set(_ORDINALS)

# Written forms taken apart before punctuation is stripped (see the module
# docstring), in this order.
_WRITTEN = (
    (re.compile(r"(?<=\d),(?=\d{3}\b)"), ""),          # 1,000
    (re.compile(r"(?<=\d)/(?=\d)"), " over "),          # 120/80
    (re.compile(r"(?<=\d)\.(?=\d)"), " point "),        # 98.6
    (re.compile(r"(?<=\d):(?=\d\d\b)"), ""),            # 3:30
    (re.compile(r"(?<=\d)-(?=\d)"), " to "),            # 10-15
    (re.compile(r"(?<=\d)%"), " percent"),              # 50%
    (re.compile(r"\$(?=\d)"), ""),                      # $5 -> 5 (the word "dollars" is rarely said)
)
_ALNUM = re.compile(r"^([a-z]+)(\d+)$|^(\d+)([a-z]+)$")
_DIGIT_ORDINAL = re.compile(r"^\d+(st|nd|rd|th)$")


def _ordinal(n):
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _split_token(tok):
    """A token as one or more number-ready tokens: "seventy-six" -> seventy
    six, "m4" -> m 4, "2nd" -> second, "two-" -> 2-. Other marked tokens are
    left whole."""
    if tok.endswith(("-", ":")):
        word = tok[:-1]
        v = _UNITS.get(word, _TEENS.get(word, _TENS.get(word)))
        return [f"{v}{tok[-1]}"] if v is not None else [tok]
    if tok == "2nd":
        return ["second"]
    if _DIGIT_ORDINAL.match(tok):
        return [tok]
    m = _ALNUM.match(tok)
    if m:
        return [g for g in m.groups() if g]
    if "-" in tok:
        parts = tok.split("-")
        if all(parts) and any(p in _NUMBER_WORDS or p.isdigit() for p in parts):
            return parts
    return [tok]


class _Number:
    """One number being read word by word (see `_numbers`)."""

    def __init__(self):
        self.total = 0          # thousands and above, already scaled
        self.hundreds = None    # this chunk's hundreds, once "hundred" is said
        self.small = 0          # the part below a hundred
        self.kind = None        # what `small` ends with: units/teen/tens/full
        self.colloquial_ok = True

    def value(self):
        return self.total + (self.hundreds or 0) + self.small

    def bare_digit(self):
        """One to nineteen and nothing else: the first half of "one twenty"
        or "nineteen eighty-four"."""
        return (self.total == 0 and self.hundreds is None and self.kind in ("units", "teen")
                and 1 <= self.small <= 19)

    def take(self, word):
        """Extend this number with `word`; False if the word starts a new one."""
        if word in _UNITS:
            if self.kind in (None, "tens"):
                self.small += _UNITS[word]
                self.kind = "units" if self.kind is None else "full"
                return True
            return False
        if word in _TEENS or word in _TENS:
            v = _TEENS.get(word) or _TENS[word]
            if self.kind is None:
                self.small, self.kind = v, "teen" if word in _TEENS else "tens"
                return True
            if self.bare_digit():                     # "one twenty" -> 120, "nineteen eighty" -> 1980
                self.hundreds, self.small = self.small * 100, v
                self.kind = "teen" if word in _TEENS else "tens"
                return True
            return False
        if word == "hundred":
            if self.kind is not None and self.hundreds is None:
                self.hundreds, self.small, self.kind = self.small * 100, 0, None
                return True
            return False
        if word in ("thousand", "million"):
            if self.kind is not None or self.hundreds is not None:
                self.total = (self.total + (self.hundreds or 0) + self.small) * _SCALES[word]
                self.hundreds, self.small, self.kind = None, 0, None
                return True
            return False
        return False


def _numbers(tokens):
    """Number-word runs in `tokens` -> digit tokens (see the module docstring)."""
    out, number = [], None

    def close():
        nonlocal number
        if number is not None:
            out.append(str(number.value()))
            number = None

    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in _ORDINALS:
            v = _ORDINALS[tok]
            if number is not None and number.kind == "tens" and v < 10:
                number.small += v
                v, number = number.value(), None
            elif tok == "hundredth" and number is not None and number.kind is not None \
                    and number.hundreds is None:
                v, number = number.value() * 100, None
            else:
                close()
            out.append(_ordinal(v))
            continue
        if tok == "a" and nxt in _SCALES and number is None:
            number = _Number()
            number.take("one")
            continue
        if tok == "and" and number is not None and number.kind is None \
                and (nxt in _UNITS or nxt in _TEENS or nxt in _TENS):
            continue                                  # "a hundred and twenty"
        if tok in _NUMBER_WORDS:
            if number is not None and number.take(tok):
                continue
            close()
            if tok in _SCALES:                        # "hundred" on its own
                number = _Number()
                number.take("one")
                number.take(tok)
                continue
            number = _Number()
            number.take(tok)
            continue
        close()
        out.append(tok)
    close()
    return out


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
    for pattern, repl in _WRITTEN:
        text = pattern.sub(repl, text)
    text = _PUNCTUATION.sub("", text)
    tokens = [_canonical(t) for t in _tokenize(text) if not _NO_WORD.match(t)]
    return _numbers([part for t in tokens for part in _split_token(t)])


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

    Numbers are canonicalized here too (module docstring). Name redaction is
    a separate step, applied before this (`redact.py`) — it needs
    case-sensitive names to fuzzy-match against, so it is never bundled into
    this function.
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
