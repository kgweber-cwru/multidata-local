"""Name redaction — the mechanical half of the two-layer de-identification
design (docs/transcription_standards.md §8, docs/asr_provider_spec.md §9).

Annotators type real names verbatim; the hypothesis transcripts contain real
names regardless of what gold does (a provider that heard "Jamie" writes
"Jamie"). So redaction happens here, once, applied identically to reference
and hypothesis, driven by the manifest's `learner_name`/`sp_name`/
`preceptor_name` columns — never by a human decision made while annotating.

Pure and stdlib-only, like `normalize.py`, and for the same reason: this is
the *other* module whose bugs are invisible in isolation (an over-broad match
silently deletes real transcript content; an under-broad one leaks a name into
a tracked file) and whose correctness is worth protecting with tests rather
than trust.

Tags are `[ROLE_NAME]` brackets on purpose, not a bespoke marker: they pass
straight through `normalize.strip_brackets`, so a redacted name is scoring-
neutral on both sides for free, via the same mechanism that already
scoring-neutralizes `[unintelligible]` and provider event tags — no separate
rule needed.

**Known limitation** (stated here so it isn't rediscovered the hard way): ASR
misspells names ("Jamey" for "Jamie"), so fuzzy matching will still miss some.
This is a best-effort first line, not a guarantee. If residual leakage proves
material, the documented escalation is timestamp-window exclusion against the
normalized record's word timings (asr_provider_spec.md §9) — robust to any
spelling, but not worth building until measurement says it's needed.
"""
import difflib
import re

ROLE_TAGS = {
    "learner": "[LEARNER_NAME]",
    "patient": "[PATIENT_NAME]",
    "preceptor": "[PRECEPTOR_NAME]",
}

# Fuzzy-matching a one- or two-letter name part against arbitrary running text
# produces false positives constantly ("Al" matching half the word "all"-like
# tokens); require exact-length-3+ before the fuzzy path even runs. Exact
# (case-insensitive) matches are unaffected by this floor.
_MIN_FUZZY_LEN = 3

_WORD = re.compile(r"[A-Za-z']+")


def _name_parts(full_name):
    """"Jamie Smith" -> ("jamie", "smith"), lowercased, empty parts dropped."""
    return tuple(p.lower() for p in _WORD.findall(full_name or "") if p)


def redact_names(text, names, threshold=0.78):
    """Replace occurrences of each role's name with `ROLE_TAGS[role]`.

    `names` — e.g. `{"learner": "Jamie Smith", "patient": "Alex Rivera",
    "preceptor": ""}` — role -> full name, from
    `manifest.names_for_case()`. A falsy name is skipped (nothing to redact
    for that role).

    Matching is per word token: exact case-insensitive match always redacts;
    a token of length >= 3 also redacts if its similarity ratio
    (`difflib.SequenceMatcher`) to any name part meets `threshold` (default
    0.78 -- chosen so a single-letter substitution like "Jamie"/"Jamey" or
    "Smith"/"Smyth", both ratio 0.8, still catches; see `tests/test_redact.py`
    for the calibration cases). Consecutive redacted tokens collapse into one
    tag (`"Jamie Smith"` -> one `[LEARNER_NAME]`, not two).

    Returns `(redacted_text, substitutions)`, where `substitutions` is a list
    of `{"role": ..., "matched": ..., "tag": ...}` dicts — one per contiguous
    redaction. `matched` carries the redacted word(s) with adjacent
    punctuation stripped (e.g. a trailing comma in "Jamie," is not part of
    `matched`), so a human can audit false positives/negatives against a clean
    name; **that makes the returned list itself as sensitive as the input
    text** — keep it in the same protected (Layer 1) location, never in a
    tracked file.

    **Known simplification:** punctuation directly attached to a redacted
    token (the comma in "Jamie,") is dropped from `redacted_text`, not
    reattached after the tag. Harmless for scoring — `normalize.scoring_base`
    strips that punctuation from both sides anyway — but if this output is
    ever shown to a person as prose rather than scored, that's a cosmetic gap
    worth fixing then, not now.
    """
    role_parts = {
        role: _name_parts(name) for role, name in names.items() if name
    }

    tokens = text.split()
    out_tokens = []
    substitutions = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        stripped = "".join(_WORD.findall(tok)).lower()
        role = _match_role(stripped, role_parts, threshold)
        if role is None:
            out_tokens.append(tok)
            i += 1
            continue

        # Absorb any immediately following tokens that also match the same
        # role, so a multi-word name collapses into one tag. `matched` records
        # the clean word content (punctuation-stripped), not the raw token --
        # see the "Known simplification" note above.
        matched = ["".join(_WORD.findall(tok))]
        j = i + 1
        while j < len(tokens):
            next_stripped = "".join(_WORD.findall(tokens[j])).lower()
            if _match_role(next_stripped, {role: role_parts[role]}, threshold) == role:
                matched.append("".join(_WORD.findall(tokens[j])))
                j += 1
            else:
                break

        tag = ROLE_TAGS.get(role, f"[{role.upper()}_NAME]")
        out_tokens.append(tag)
        substitutions.append({"role": role, "matched": " ".join(matched), "tag": tag})
        i = j

    return " ".join(out_tokens), substitutions


def _match_role(word, role_parts, threshold):
    """First role whose name `word` matches (exact or, if long enough,
    fuzzy). Deterministic role order (dict insertion order) so an ambiguous
    word always resolves the same way rather than depending on iteration
    order."""
    if not word:
        return None
    for role, parts in role_parts.items():
        if word in parts:
            return role
    if len(word) >= _MIN_FUZZY_LEN:
        for role, parts in role_parts.items():
            for part in parts:
                if len(part) < _MIN_FUZZY_LEN:
                    continue
                if difflib.SequenceMatcher(None, word, part).ratio() >= threshold:
                    return role
    return None
