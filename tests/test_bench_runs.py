import time

from multidata.bench_runs import (
    cache_get,
    cache_key,
    cache_put,
    config_hash,
    make_run_id,
    write_run,
)


class TestConfigHash:
    def test_stable_across_key_order(self):
        a = {"engine": "whisperx", "model": "medium"}
        b = {"model": "medium", "engine": "whisperx"}
        assert config_hash(a) == config_hash(b)

    def test_changes_with_content(self):
        assert config_hash({"model": "medium"}) != config_hash({"model": "large-v3"})

    def test_length_is_configurable(self):
        assert len(config_hash({"a": 1}, length=12)) == 12
        assert len(config_hash({"a": 1}, length=8)) == 8


class TestMakeRunId:
    def test_is_eye_navigable(self):
        when = time.strptime("2026-08-26T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ")
        run_id = make_run_id("whisperx_disfluent", "medium", {"model": "medium"}, when=when)
        assert run_id.startswith("20260826T120000Z_whisperx_disfluent_medium_")

    def test_same_config_same_timestamp_gives_same_id(self):
        when = time.strptime("2026-08-26T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ")
        a = make_run_id("whisperx", "medium", {"model": "medium"}, when=when)
        b = make_run_id("whisperx", "medium", {"model": "medium"}, when=when)
        assert a == b

    def test_different_config_same_timestamp_disambiguates(self):
        when = time.strptime("2026-08-26T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ")
        a = make_run_id("whisperx", "medium", {"model": "medium"}, when=when)
        b = make_run_id("whisperx", "medium", {"model": "large-v3"}, when=when)
        assert a != b

    def test_slugs_unsafe_characters(self):
        run_id = make_run_id("some/engine", "a model", {})
        assert "/" not in run_id
        assert " " not in run_id


class TestCache:
    def test_miss_returns_none(self, tmp_path):
        key = cache_key("abc123", "whisperx", {"model": "medium"})
        assert cache_get(tmp_path, key) is None

    def test_put_then_get_round_trips(self, tmp_path):
        key = cache_key("abc123", "whisperx", {"model": "medium"})
        cache_put(tmp_path, key, {"segments": []})
        assert cache_get(tmp_path, key) == {"segments": []}

    def test_different_params_different_key(self):
        k1 = cache_key("abc123", "whisperx", {"model": "medium"})
        k2 = cache_key("abc123", "whisperx", {"model": "large-v3"})
        assert k1 != k2

    def test_different_audio_different_key(self):
        k1 = cache_key("abc123", "whisperx", {"model": "medium"})
        k2 = cache_key("def456", "whisperx", {"model": "medium"})
        assert k1 != k2

    def test_different_provider_different_key(self):
        k1 = cache_key("abc123", "whisperx", {"model": "medium"})
        k2 = cache_key("abc123", "faster_whisper", {"model": "medium"})
        assert k1 != k2

    def test_corrupt_cache_file_is_a_miss_not_a_crash(self, tmp_path):
        key = cache_key("abc123", "whisperx", {"model": "medium"})
        path = tmp_path / f"{key}.json"
        path.write_text("{not valid json")
        assert cache_get(tmp_path, key) is None


class TestWriteRun:
    def test_writes_three_files(self, tmp_path):
        d = write_run(
            tmp_path, "20260826T120000Z_whisperx_medium_abcd1234",
            config={"engine": "whisperx", "model": "medium"},
            record={"language": "en", "segments": []},
            scores={"wer": 0.29},
        )
        assert (d / "config.resolved.yaml").exists()
        assert (d / "record.json").exists()
        assert (d / "scores.json").exists()

    def test_record_content_round_trips(self, tmp_path):
        d = write_run(
            tmp_path, "run1",
            config={"engine": "whisperx"},
            record={"language": "en", "segments": [{"text": "hi"}]},
            scores={},
        )
        import json
        with open(d / "record.json") as f:
            assert json.load(f) == {"language": "en", "segments": [{"text": "hi"}]}

    def test_ls_runs_dir_is_self_explanatory(self, tmp_path):
        # The actual "done when" from the implementation plan: run
        # directories should be readable at a glance, not opaque hashes.
        write_run(tmp_path, "20260826T120000Z_whisperx_medium_abcd1234",
                  config={}, record={"segments": []}, scores={})
        names = [p.name for p in tmp_path.iterdir()]
        assert names == ["20260826T120000Z_whisperx_medium_abcd1234"]
