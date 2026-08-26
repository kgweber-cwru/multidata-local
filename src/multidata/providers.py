"""Capability registry loader (docs/asr_provider_spec.md §2).

`benchmarks/configs/providers.yaml` is the single artifact both a human and
the scorer read to know what a provider can and can't do: diarizes on its
own? word-level timing? which glossary mechanism, and how lossy? A benchmark
result you can't attribute to an exact, checked capability claim is exactly
the kind of noise pipeline doc §8d already warns about, one level up the
stack -- so the loader validates shape, it doesn't just parse YAML.

Every field here is required to be *present* (even if its value is `null`) --
absence should never be confused with "not applicable." A provider with no
glossary mechanism says `glossary_mechanism: none` in the file; it doesn't
omit the key.
"""
import yaml

REQUIRED_FIELDS = (
    "kind",                  # "local" | "cloud"
    "diarizes_internally",
    "word_timings",
    "glossary_mechanism",    # keyterm | word_boost | phrase_set | initial_prompt | none
    "glossary_limit",        # max terms, or null if uncapped/not applicable
    "disfluency_param",      # request param that keeps fillers, or null
    "best_disfluency_config",  # this provider's tuned config, or null (spec §8)
    "speaker_hint_param",
    "formatting_off_param",
    "long_audio",            # {sync_limit_s, async_path} or null (no vendor limit)
    "baa_available",         # true/false/null(unknown) -- a selection criterion, not a footnote
    "no_train_flag",
    "cost_per_minute",
    "verified",              # has this row been checked against live docs?
    "checked_on",            # date string, or null if unverified
)


def load_providers(path):
    """Parse and validate `providers.yaml` -> {name: entry}. Raises
    `ValueError` naming every missing field across every entry at once,
    rather than stopping at the first bad row -- a registry edit often touches
    several providers together, and single-error-at-a-time feedback is the
    slow way to fix that."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    providers = data.get("providers", {})

    errors = []
    for name, entry in providers.items():
        problems = validate_provider(name, entry)
        errors.extend(f"{name}: {p}" for p in problems)
    if errors:
        raise ValueError("providers.yaml is invalid:\n  " + "\n  ".join(errors))

    return providers


def validate_provider(name, entry):
    """List of problem strings for one entry; empty means valid."""
    if not isinstance(entry, dict):
        return ["entry is not a mapping"]
    return [f"missing required field {f!r}" for f in REQUIRED_FIELDS if f not in entry]


def unverified(providers):
    """Names of every cloud provider whose capability row hasn't been checked
    against live vendor docs -- the thing implementation plan Phase 0.4 must
    clear to zero before any adapter is built against this file."""
    return sorted(
        name for name, entry in providers.items()
        if entry.get("kind") == "cloud" and not entry.get("verified", False)
    )
