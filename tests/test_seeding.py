"""Draft-seeded gold (standards §9 route B): seed -> kit -> checks -> gold -> benchmark.

No GPU, no real media: a hand-made seed transcript stands in for the
whisperx_disfluent/large-v3 one.
"""
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "annotation"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks"))
pympi = pytest.importorskip("pympi")

from multidata import seeding  # noqa: E402
import make_kit  # noqa: E402
import submit_checks  # noqa: E402


def _w(word, a, b, spk):
    return {"word": word, "start": a, "end": b, "score": 0.9, "speaker": spk}


SEED = {"segments": [{"words": [
    _w("Hi,", 1.0, 1.3, "SPEAKER_01"), _w("I'm", 1.35, 1.5, "SPEAKER_01"),
    _w("the", 1.55, 1.7, "SPEAKER_01"), _w("student.", 1.75, 2.2, "SPEAKER_01"),
    _w("Hello", 2.5, 2.9, "SPEAKER_00"), _w("there", 2.95, 3.3, "SPEAKER_00"),
    _w("So", 6.0, 6.2, "SPEAKER_01"),                       # long pause: new utterance
    _w("Station", 8.0, 8.4, "SPEAKER_02"), _w("closing", 8.5, 9.0, "SPEAKER_02"),
]}]}


def test_utterances_split_by_speaker_and_pause():
    u = seeding.utterances(SEED)
    assert [x[0] for x in u] == ["SPEAKER_01", "SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    assert u[0] == ("SPEAKER_01", 1.0, 2.2, "Hi, I'm the student.")
    assert seeding.utterances(SEED) == u                     # deterministic


def test_parse_roles_rejects_non_role():
    assert seeding.parse_roles("SPEAKER_00=patient, SPEAKER_01=LEARNER") == \
        {"SPEAKER_00": "PATIENT", "SPEAKER_01": "LEARNER"}
    with pytest.raises(ValueError):
        seeding.parse_roles("SPEAKER_00=NOTES")


def test_seeded_annotations_roles_and_unassigned():
    tiers = make_kit.seeded_annotations(seeding.utterances(SEED),
                                        {"SPEAKER_00": "PATIENT", "SPEAKER_01": "LEARNER"})
    assert [t[2] for t in tiers["LEARNER"]] == ["Hi, I'm the student.", "So"]
    assert tiers["PATIENT"] == [(2500, 3300, "Hello there")]
    assert tiers[seeding.UNASSIGNED_TIER] == [(8000, 9000, "Station closing")]


def test_two_speakers_on_one_role_are_merged_not_overlapped():
    utts = [("SPEAKER_00", 1.0, 3.0, "a b"), ("SPEAKER_03", 2.5, 4.0, "c d")]
    tiers = make_kit.seeded_annotations(utts, {"SPEAKER_00": "PATIENT", "SPEAKER_03": "PATIENT"})
    assert tiers["PATIENT"] == [(1000, 4000, "a b c d")]


@pytest.fixture
def seeded_kit(tmp_path):
    """A seeded pass-1 .eaf + kit.json, as make_kit.py --seed --roles writes them."""
    seed = tmp_path / "9_whisperx_disfluent_large-v3.json"
    seed.write_text(json.dumps(SEED))
    roles = {"SPEAKER_00": "PATIENT", "SPEAKER_01": "LEARNER"}
    prov = seeding.provenance(seed, roles)
    kit = tmp_path / "kit"
    kit.mkdir()
    eaf = kit / "c1.pass1.eaf"
    make_kit.write_eaf(eaf, "c1", seeded=make_kit.seeded_annotations(
        seeding.utterances(SEED), roles), seed_prov=prov)
    (kit / "kit.json").write_text(json.dumps({"recording_id": "c1", **prov}))
    return kit, eaf, prov


def test_seed_provenance_travels_in_the_eaf(seeded_kit):
    _, eaf, prov = seeded_kit
    got = seeding.eaf_provenance(eaf)
    assert got["pass1_source"] == "seeded" and got["seed_sha256"] == prov["seed_sha256"]
    tiers = pympi.Elan.Eaf(str(eaf)).get_tier_names()
    assert "UNASSIGNED" in tiers and "LEARNER" in tiers


def test_submit_refuses_leftover_unassigned(seeded_kit):
    _, eaf, _ = seeded_kit
    problems, _ = submit_checks.check(submit_checks.read_annotations(eaf))
    assert any("UNASSIGNED" in p for p in problems)
    e = pympi.Elan.Eaf(str(eaf))
    e.remove_all_annotations_from_tier("UNASSIGNED")
    e.to_file(str(eaf))
    problems, _ = submit_checks.check(submit_checks.read_annotations(eaf))
    assert problems == []


def test_eaf_to_gold_provenance_cross_check(seeded_kit, tmp_path):
    import eaf_to_gold
    kit, eaf, prov = seeded_kit
    assert eaf_to_gold.pass1_provenance(eaf, kit)["seed_sha256"] == prov["seed_sha256"]
    assert eaf_to_gold.pass1_provenance(eaf, None)["pass1_source"] == "seeded"   # header copy
    blind_kit = tmp_path / "blind"
    blind_kit.mkdir()
    (blind_kit / "kit.json").write_text(json.dumps({"pass1_source": "blind"}))
    with pytest.raises(SystemExit):
        eaf_to_gold.pass1_provenance(eaf, blind_kit)                             # contradiction


def test_gold_meta_round_trip_and_legacy_default(tmp_path):
    assert seeding.read_gold_meta("x", tmp_path)["pass1_source"] == "blind"
    seeding.write_gold_meta("x", tmp_path, {"recording_id": "x", "pass1_source": "seeded",
                                            "seed_engine": "whisperx_disfluent"})
    assert seeding.read_gold_meta("x", tmp_path)["seed_engine"] == "whisperx_disfluent"


def test_benchmark_refuses_seed_engine_only():
    import run_benchmark
    meta = {"recording_id": "x", "pass1_source": "seeded", "seed_engine": "whisperx_disfluent",
            "seed_model": "large-v3"}
    with pytest.raises(SystemExit):
        run_benchmark.refuse_seed_engine("whisperx_disfluent", meta)
    run_benchmark.refuse_seed_engine("faster_whisper", meta)                     # allowed
    run_benchmark.refuse_seed_engine("whisperx_disfluent", {"pass1_source": "blind"})


def test_ledger_header_upgrade_keeps_rows(tmp_path):
    import run_benchmark
    old = tmp_path / "runs.csv"
    old_fields = [f for f in run_benchmark.FIELDS if f != "gold_pass1"]
    with open(old, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=old_fields)
        w.writeheader()
        w.writerow({k: "v" for k in old_fields})
    run_benchmark.record({k: "n" for k in run_benchmark.FIELDS}, results_path=old)
    rows = list(csv.DictReader(open(old)))
    assert list(rows[0].keys()) == run_benchmark.FIELDS
    assert rows[0]["gold_pass1"] == "" and rows[0]["recording_id"] == "v" and rows[1]["gold_pass1"] == "n"


def test_kit_seed_transcripts_lists_only_seeded_kits(tmp_path):
    kits, data = tmp_path / "kits", tmp_path / "data"
    for recording, ann, kit in [("c1", "a", {"recording_id": "c1", "pass1_source": "seeded",
                                        "seed_transcript": "9_x_y.json"}),
                           ("c2", "b", {"recording_id": "c2"})]:
        (kits / recording / ann).mkdir(parents=True)
        (kits / recording / ann / "kit.json").write_text(json.dumps(kit))
    assert seeding.kit_seed_transcripts(kits, data) == \
        {(data / "transcripts" / "c1" / "9_x_y.json").resolve()}


def test_pipeline_engine_is_the_seed_engine():
    asr = pytest.importorskip("multidata.asr")
    assert (asr.DEFAULT_ENGINE, asr.DEFAULT_MODEL) == (seeding.SEED_ENGINE, seeding.SEED_MODEL)


def test_whisperx_family_uses_pipeline_rttm(tmp_path, monkeypatch):
    asr = pytest.importorskip("multidata.asr")
    seen = {}

    def fake_engine(audio_path, **kw):
        seen.update(kw)
        return {"segments": []}

    monkeypatch.setitem(asr.ENGINES, "whisperx_disfluent", fake_engine)
    monkeypatch.setattr(asr, "_diarize_and_merge", lambda r, *a, **k: seen.setdefault("merged", a[1]) and r)
    rttm = tmp_path / "x.rttm"
    asr.transcribe("a.wav", engine="whisperx_disfluent", diarize_rttm=rttm)
    assert seen["diarize"] is False and seen["merged"] == rttm
    seen.clear()
    asr.transcribe("a.wav", engine="whisperx_disfluent")            # no RTTM: diarizes itself
    assert "diarize" not in seen and "merged" not in seen
