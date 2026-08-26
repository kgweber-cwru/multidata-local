"""The normalized ASR record — schema stamping and validation
(docs/asr_provider_spec.md §4).

Not a new shape: this formalizes what `asr.py`'s engines already return
(`{language, segments: [{start, end, text, words: [{word, start, end, score,
speaker}]}]}`), so every existing engine's output keeps validating unchanged.
What's new is making the contract explicit and machine-checked, so a future
provider adapter can't silently drift from it.

A validator, not a class hierarchy (implementation plan Phase 1.5) — the
record is still a plain dict everywhere else in the codebase (`elan.py`,
`run_benchmark.py`), and wrapping it in a schema class would mean touching
every consumer for no behavioral gain.
"""

SCHEMA_VERSION = "1"


def finalize_record(result, engine, word_timing=True, chunked=False, provenance=None):
    """Stamp the fields every normalized record must carry
    (`schema_version`, `engine`, `word_timing`, `chunked`, `provenance`),
    defaulting whatever a raw engine function didn't already set. Returns a
    new dict; does not mutate `result`.

    `word_timing=False` is the honest answer for a provider that only returns
    segment-level output -- never set it True to paper over missing word
    timestamps (asr_provider_spec.md §4's "never synthesize word timings"
    rule). `chunked=True` means the caller split long audio and stitched the
    result back together; record it so a boundary-timestamp error is visible
    rather than silently blended into normal ASR error.
    """
    record = dict(result)
    record.setdefault("schema_version", SCHEMA_VERSION)
    record.setdefault("engine", engine)
    record.setdefault("word_timing", word_timing)
    record.setdefault("chunked", chunked)
    record.setdefault("provenance", dict(provenance) if provenance else {})
    return record


def validate_record(record):
    """List of problem descriptions; empty list means valid.

    Deliberately lenient about *when in the pipeline* a record was captured:
    a record from a raw engine function (pre-diarization-merge, no
    `schema_version`/`provenance` yet, words with no `speaker`) and one from
    the full `asr.transcribe()` path (post-merge, fully stamped) are both
    valid normalized records at their respective stages -- this checks
    structural shape, not pipeline completeness. `speaker` is intentionally
    optional per word for that reason.
    """
    problems = []

    if not isinstance(record, dict):
        return ["record is not a dict"]

    for field, expected_type in (
        ("language", str),
        ("segments", list),
    ):
        if field not in record:
            problems.append(f"missing required field {field!r}")
        elif not isinstance(record[field], expected_type):
            problems.append(f"{field!r} must be a {expected_type.__name__}")

    for field, expected_type in (
        ("schema_version", str),
        ("engine", str),
        ("word_timing", bool),
        ("chunked", bool),
        ("provenance", dict),
    ):
        if field in record and not isinstance(record[field], expected_type):
            problems.append(f"{field!r} must be a {expected_type.__name__}")

    word_timing = record.get("word_timing", True)

    for i, segment in enumerate(record.get("segments", [])):
        if not isinstance(segment, dict):
            problems.append(f"segments[{i}] is not a dict")
            continue
        for field in ("start", "end", "text"):
            if field not in segment:
                problems.append(f"segments[{i}] missing {field!r}")
        words = segment.get("words")
        if words is None:
            if word_timing:
                problems.append(
                    f"segments[{i}] has no 'words' but word_timing is True/absent"
                )
            continue
        if not isinstance(words, list):
            problems.append(f"segments[{i}]['words'] is not a list")
            continue
        for j, word in enumerate(words):
            if not isinstance(word, dict):
                problems.append(f"segments[{i}]['words'][{j}] is not a dict")
                continue
            for field in ("word", "start", "end"):
                if field not in word:
                    problems.append(f"segments[{i}]['words'][{j}] missing {field!r}")
            if "speaker" in word and not isinstance(word["speaker"], str):
                problems.append(f"segments[{i}]['words'][{j}]['speaker'] must be a str")

    return problems
