from pathlib import Path

import pytest
import yaml

from multidata import asr
from multidata.providers import REQUIRED_FIELDS, load_providers, unverified, validate_provider

ROOT = Path(__file__).resolve().parent.parent
REAL_PROVIDERS_YAML = ROOT / "benchmarks" / "configs" / "providers.yaml"

MINIMAL_VALID_ENTRY = {f: None for f in REQUIRED_FIELDS}


class TestValidateProvider:
    def test_minimal_valid_entry_has_no_problems(self):
        assert validate_provider("x", MINIMAL_VALID_ENTRY) == []

    def test_missing_field_is_reported(self):
        entry = dict(MINIMAL_VALID_ENTRY)
        del entry["baa_available"]
        problems = validate_provider("x", entry)
        assert any("baa_available" in p for p in problems)

    def test_not_a_mapping_is_reported(self):
        assert validate_provider("x", "not a dict") == ["entry is not a mapping"]

    def test_null_values_are_fine(self):
        # Required means present, not non-null -- "no glossary mechanism" is
        # a legitimate null, not a validation failure.
        entry = dict(MINIMAL_VALID_ENTRY, glossary_mechanism=None)
        assert validate_provider("x", entry) == []


class TestLoadProviders:
    def test_rejects_a_provider_missing_a_required_field(self, tmp_path):
        bad = tmp_path / "providers.yaml"
        bad.write_text(yaml.dump({"providers": {"x": {"kind": "local"}}}))
        with pytest.raises(ValueError, match="x:"):
            load_providers(bad)

    def test_reports_every_bad_entry_at_once(self, tmp_path):
        bad = tmp_path / "providers.yaml"
        bad.write_text(yaml.dump({
            "providers": {"a": {"kind": "local"}, "b": {"kind": "cloud"}},
        }))
        with pytest.raises(ValueError) as exc_info:
            load_providers(bad)
        assert "a:" in str(exc_info.value)
        assert "b:" in str(exc_info.value)

    def test_empty_file_loads_to_empty_dict(self, tmp_path):
        empty = tmp_path / "providers.yaml"
        empty.write_text("")
        assert load_providers(empty) == {}


class TestUnverified:
    def test_flags_unverified_cloud_only(self):
        providers = {
            "local_one": {"kind": "local", "verified": False},
            "cloud_verified": {"kind": "cloud", "verified": True},
            "cloud_unverified": {"kind": "cloud", "verified": False},
        }
        assert unverified(providers) == ["cloud_unverified"]

    def test_missing_verified_key_counts_as_unverified(self):
        providers = {"cloud_x": {"kind": "cloud"}}
        assert unverified(providers) == ["cloud_x"]


class TestRealProvidersYaml:
    """Regression tests against the actual draft file -- these should keep
    passing as Kate corrects it; they check structure, not editorial content
    (verified/checked_on/cost figures), so they don't need updating every
    time a row gets corrected."""

    def test_loads_and_validates(self):
        providers = load_providers(REAL_PROVIDERS_YAML)
        assert providers

    def test_local_providers_match_asr_engines_exactly(self):
        """The registry and the code must never silently diverge: every
        engine asr.py actually registers needs a capability row, and every
        local row in the registry needs a real engine behind it."""
        providers = load_providers(REAL_PROVIDERS_YAML)
        local_names = {name for name, entry in providers.items() if entry["kind"] == "local"}
        assert local_names == set(asr.ENGINES)

    def test_verified_rows_carry_a_check_date(self):
        """A permanent invariant, unlike a snapshot of *which* rows are
        verified (which is expected to keep changing as Kate checks vendor
        docs, Phase 0.4): whenever a row claims verified: true, checked_on
        must be a real date, not null. A verified claim with no date attached
        is exactly the kind of thing that looks fine and isn't."""
        providers = load_providers(REAL_PROVIDERS_YAML)
        unstamped = [name for name, entry in providers.items()
                     if entry.get("verified") and not entry.get("checked_on")]
        assert unstamped == []

    def test_at_least_one_cloud_row_is_unverified(self):
        # Loose on purpose: today every cloud row is a draft (Phase 0.4 is
        # unstarted), but this only needs to hold until the *last* one gets
        # checked -- unlike the exact-match version this replaced, verifying
        # one more provider can never break it.
        providers = load_providers(REAL_PROVIDERS_YAML)
        assert unverified(providers)
