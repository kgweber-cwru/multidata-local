"""Run identity, on-disk layout, and response caching for the ASR benchmark
wing (docs/asr_provider_implementation_plan.md Phase 2, "Bookkeeping").

Built before any cloud adapter, on purpose: caching is the difference between
iterating for free and iterating on the meter, and per-run persistence is
what keeps a growing `benchmarks/runs/` directory legible instead of turning
into a pile of CSV rows nobody can trace back to what actually produced them.

Two separate concerns, two separate locations:

- **The cache** (`cache_get`/`cache_put`) is content-addressed by
  `(audio_sha256, provider, params)` and lives under one shared root
  (`benchmarks/cache/` by convention) -- its job is to never pay for the same
  computation twice, for as many runs as end up reusing it.
- **A run directory** (`write_run`) is a full, self-contained historical
  snapshot of one scoring invocation -- what config was used, what the
  engine returned, what it scored -- even if the engine call itself was
  served from cache. Runs don't share files; the cache is what they share.
"""
import hashlib
import json
import re
import time
from pathlib import Path

_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


def _slug(value):
    """Filesystem/ID-safe. Engine and model names shouldn't need escaping to
    appear in a directory name today, but nothing guarantees a future
    provider name or model string won't contain something that does."""
    return _SLUG.sub("_", str(value))


def config_hash(config, length=8):
    """Stable short hash over a resolved-config dict, independent of key
    order -- same convention as `multidata.glossary.content_hash`, for the
    same reason: two dicts with the same content but different construction
    order must hash identically, or every cache/run-id comparison downstream
    becomes unreliable."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


def make_run_id(engine, model, config, when=None):
    """`<UTC timestamp>_<engine>_<model>_<8-char config hash>` -- eye-
    navigable (`ls benchmarks/runs/` sorts chronologically and reads like a
    log) with the hash only there to disambiguate two runs of the same
    engine/model in the same second, not to be the primary way a human tells
    runs apart.

    `when`, if given, is a `time.struct_time` in UTC (as from `time.gmtime()`)
    -- exposed for deterministic tests, not meant to be passed a local time.
    """
    when = when or time.gmtime()
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", when)
    return f"{timestamp}_{_slug(engine)}_{_slug(model)}_{config_hash(config)}"


def run_dir(root, run_id):
    path = Path(root) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_run(root, run_id, config, record, scores):
    """Write the three files one run directory holds: `config.resolved.yaml`
    (every parameter actually used, not just what was passed -- defaults
    included), `record.json` (the normalized ASR record,
    `multidata.records`), and `scores.json`. Returns the directory.

    A separate raw provider response belongs alongside these
    (asr_provider_spec.md §4) once a provider actually has one distinct from
    its normalized record -- today's local engines don't (`asr.transcribe()`'s
    output already is both), so there's nothing to write yet. A cloud adapter
    should write `raw.json` into this same directory itself.
    """
    import yaml

    d = run_dir(root, run_id)
    with open(d / "config.resolved.yaml", "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)
    with open(d / "record.json", "w") as f:
        json.dump(record, f, indent=2)
    with open(d / "scores.json", "w") as f:
        json.dump(scores, f, indent=2)
    return d


def cache_key(audio_sha256, provider, params):
    """`(audio_sha256, provider, params)` -> one content-addressed key.
    `params` should be the values actually resolved/used, not raw CLI
    defaults -- two differently-worded but equivalent calls should hit the
    same entry, and two calls that differ in any real way should never
    collide. Truncating the audio hash to 16 hex chars keeps keys readable
    without meaningfully increasing collision risk for a benchmark-scale
    corpus (a handful to a few hundred cases)."""
    return f"{provider}_{audio_sha256[:16]}_{config_hash(params)}"


def cache_path(cache_root, key):
    return Path(cache_root) / f"{key}.json"


def cache_get(cache_root, key):
    """The cached result dict, or `None` on a miss -- including a corrupt or
    unreadable cache file. A cache is an optimization, not a source of truth:
    any problem reading it should fall through to recomputing, never crash a
    run over a bad cache entry."""
    path = cache_path(cache_root, key)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def cache_put(cache_root, key, result):
    path = cache_path(cache_root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    return path
