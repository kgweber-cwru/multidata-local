"""Canonical domain-term glossary — loader and content hashing
(docs/asr_provider_spec.md §3, docs/transcription_standards.md §6).

One file, three consumers: annotators spell domain terms from it
(transcription_standards.md §6), ASR provider adapters render it into
whichever term-biasing mechanism they have (`keyterm`/`word_boost`/
`phrase_set`/`initial_prompt`/none), and the benchmark hashes it so an edit to
the list is never silently absorbed into a score -- `glossary_sha` in run
provenance is exactly this hash, and a comparison across two different hashes
should never be reported as apples-to-apples.
"""
import hashlib
import json

import yaml

REQUIRED_TERM_FIELDS = ("term",)


def load_glossary(path):
    """Parse and validate `glossary.yaml` -> list of term dicts. Each entry
    needs at least a non-empty `term`; `weight`/`category`/`pronunciation` are
    optional per-provider hints (asr_provider_spec.md §3) that a renderer may
    or may not use."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    terms = data.get("terms", [])

    errors = []
    for i, term in enumerate(terms):
        if not isinstance(term, dict):
            errors.append(f"terms[{i}] is not a mapping")
            continue
        if not term.get("term"):
            errors.append(f"terms[{i}] has no non-empty 'term'")
    if errors:
        raise ValueError("glossary.yaml is invalid:\n  " + "\n  ".join(errors))

    return terms


def content_hash(terms):
    """Stable sha256 over term *content*, independent of file formatting --
    whitespace, YAML key order, or a comment change never moves this hash;
    adding, removing, or editing a term always does."""
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def glossary_sha(path):
    """The `glossary_sha` provenance value for a run using this file as-is."""
    return content_hash(load_glossary(path))
