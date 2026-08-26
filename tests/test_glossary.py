from pathlib import Path

import pytest
import yaml

from multidata.glossary import content_hash, glossary_sha, load_glossary

ROOT = Path(__file__).resolve().parent.parent
REAL_GLOSSARY_YAML = ROOT / "benchmarks" / "configs" / "glossary.yaml"


class TestLoadGlossary:
    def test_real_glossary_loads_and_is_non_empty(self):
        terms = load_glossary(REAL_GLOSSARY_YAML)
        assert len(terms) > 0
        assert all(t.get("term") for t in terms)

    def test_missing_term_field_raises(self, tmp_path):
        bad = tmp_path / "glossary.yaml"
        bad.write_text(yaml.dump({"terms": [{"category": "unit"}]}))
        with pytest.raises(ValueError, match="term"):
            load_glossary(bad)

    def test_empty_term_string_raises(self, tmp_path):
        bad = tmp_path / "glossary.yaml"
        bad.write_text(yaml.dump({"terms": [{"term": ""}]}))
        with pytest.raises(ValueError):
            load_glossary(bad)

    def test_empty_file_loads_to_empty_list(self, tmp_path):
        empty = tmp_path / "glossary.yaml"
        empty.write_text("")
        assert load_glossary(empty) == []


class TestContentHash:
    def test_stable_across_key_order(self):
        a = [{"term": "mmHg", "weight": 1.0}]
        b = [{"weight": 1.0, "term": "mmHg"}]
        assert content_hash(a) == content_hash(b)

    def test_changes_when_a_term_is_added(self):
        a = [{"term": "mmHg"}]
        b = [{"term": "mmHg"}, {"term": "tachycardia"}]
        assert content_hash(a) != content_hash(b)

    def test_changes_when_a_weight_changes(self):
        a = [{"term": "mmHg", "weight": 1.0}]
        b = [{"term": "mmHg", "weight": 0.5}]
        assert content_hash(a) != content_hash(b)

    def test_deterministic(self):
        terms = [{"term": "mmHg", "weight": 1.0}]
        assert content_hash(terms) == content_hash(terms)


class TestGlossarySha:
    def test_matches_content_hash_of_loaded_terms(self):
        assert glossary_sha(REAL_GLOSSARY_YAML) == \
            content_hash(load_glossary(REAL_GLOSSARY_YAML))

    def test_unaffected_by_comment_only_edit(self, tmp_path):
        f1 = tmp_path / "a.yaml"
        f1.write_text(yaml.dump({"terms": [{"term": "mmHg"}]}))
        f2 = tmp_path / "b.yaml"
        f2.write_text("# a comment\n" + yaml.dump({"terms": [{"term": "mmHg"}]}))
        assert glossary_sha(f1) == glossary_sha(f2)
