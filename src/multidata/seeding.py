"""Draft-seeded gold: starting pass 1 from a machine transcript.

The fallback when there aren't enough annotators for blind gold
(docs/transcription_standards.md §9, "Route B"). Instead of an empty
template, the pass-1 annotator gets the template's tiers already filled with
utterances from a fixed machine transcript, and corrects them against the
audio; pass 2 is adjudication by a different person, as for blind gold.

Gold made this way leans toward the engine that seeded it (people accept
plausible machine errors), so:

  - the seed is always the same engine and model (`SEED_ENGINE`,
    `SEED_MODEL`), recorded with a hash of the exact transcript used;
  - the benchmark refuses to score the seed engine against seeded gold, and
    reports seeded and blind gold separately (`run_benchmark.py`).

Reproducible: given the same transcript file and the same role map, the
seeded pass-1 file is identical (`utterances`, `make_kit.py --seed`).

Runs in md-speech (making a seed transcript needs the GPU); everything else
here is standard library.
"""
import hashlib
import json
from pathlib import Path

# The one engine and model that seeds every draft-seeded gold file. It keeps
# disfluencies (the standards keep them), and large-v3 is the most accurate
# local model. Changing this is a standards change (§12): seeded gold made
# with different seeds must not be pooled without saying so.
SEED_ENGINE = "whisperx_disfluent"
SEED_MODEL = "large-v3"

MAX_GAP_S = 0.6      # a same-speaker pause longer than this starts a new utterance
MAX_UTTERANCE_S = 20.0

ROLE_TIERS = ("LEARNER", "PATIENT", "PRECEPTOR", "ANNOUNCEMENT", "OUTSIDE_ROOM")
UNASSIGNED_TIER = "UNASSIGNED"   # seeded speech whose speaker had no role; must end empty


def seed_transcript_path(case_id, camera, data_dir):
    return Path(data_dir) / "transcripts" / str(case_id) / f"{camera}_{SEED_ENGINE}_{SEED_MODEL}.json"


def make_seed_transcript(wav, out_path, rttm=None, device="cuda"):
    """Run the seed engine on a case's WAV (GPU, a few minutes per 20 min of
    audio). Skips if `out_path` already exists: the seed must not change
    under a kit once it's been used."""
    out_path = Path(out_path)
    if out_path.exists():
        return out_path
    from multidata import asr
    from multidata.tools import put_env_on_path

    put_env_on_path()             # whisperx runs "ffmpeg" by name
    asr.transcribe(str(wav), out_path=str(out_path), engine=SEED_ENGINE, model_name=SEED_MODEL,
                   device=device, align_device=device, diarize_device=device,
                   diarize_rttm=str(rttm) if rttm else None)
    return out_path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _words(data):
    words = data.get("words") or [w for s in data.get("segments", []) for w in s.get("words", [])]
    return [w for w in words if w.get("word", "").strip() and "start" in w and "end" in w]


def utterances(data, max_gap_s=MAX_GAP_S, max_len_s=MAX_UTTERANCE_S):
    """[(speaker, start_s, end_s, text)] from a word-level transcript: runs of
    words by one speaker, split at pauses over `max_gap_s` and at
    `max_len_s`, in time order. Deterministic for a given file."""
    out, cur = [], None
    for w in sorted(_words(data), key=lambda w: (w["start"], w["end"])):
        spk = w.get("speaker") or "UNKNOWN"
        text = w["word"].strip()
        if (cur and spk == cur[0] and w["start"] - cur[2] <= max_gap_s
                and w["end"] - cur[1] <= max_len_s):
            cur[2] = max(cur[2], w["end"])
            cur[3].append(text)
        else:
            if cur:
                out.append(cur)
            cur = [spk, w["start"], w["end"], [text]]
    if cur:
        out.append(cur)
    return [(s, round(a, 3), round(b, 3), " ".join(t)) for s, a, b, t in out if b > a]


def speaker_overview(utts, n_examples=3):
    """{speaker: {"talk_s", "utterances", "first", "examples"}} to help decide
    which role each diarized speaker is. Contains transcript text (possibly
    names): print it on tofino, don't share it."""
    out = {}
    for spk, a, b, text in utts:
        s = out.setdefault(spk, {"talk_s": 0.0, "utterances": 0, "first": a, "examples": []})
        s["talk_s"] += b - a
        s["utterances"] += 1
        if len(s["examples"]) < n_examples and len(text.split()) >= 4:
            s["examples"].append(f"[{int(a // 60)}:{a % 60:04.1f}] {text[:90]}")
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["talk_s"]))


def parse_roles(spec):
    """'SPEAKER_00=PATIENT,SPEAKER_01=LEARNER' -> dict; roles checked."""
    roles = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        spk, _, role = part.partition("=")
        role = role.strip().upper()
        if role not in ROLE_TIERS:
            raise ValueError(f"{role!r} is not a role tier ({', '.join(ROLE_TIERS)})")
        roles[spk.strip()] = role
    return roles


def provenance(transcript_path, roles):
    """What a seeded kit records about its seed (kit.json, the .eaf header,
    and in the end the gold's .gold.json)."""
    return {"pass1_source": "seeded", "seed_engine": SEED_ENGINE, "seed_model": SEED_MODEL,
            "seed_transcript": Path(transcript_path).name,
            "seed_sha256": sha256(transcript_path), "roles": roles,
            "max_gap_s": MAX_GAP_S, "max_utterance_s": MAX_UTTERANCE_S}


def describe(prov):
    """One line for logs and reports."""
    if not prov or prov.get("pass1_source") != "seeded":
        return "blind"
    return f"seeded from {prov['seed_engine']}/{prov['seed_model']} ({prov['seed_sha256'][:12]})"


def eaf_provenance(eaf_path):
    """Seed provenance written into a seeded pass-1 .eaf's header by
    make_kit.py, or {} for a file without it (blind, or ELAN dropped it)."""
    import xml.etree.ElementTree as ET

    props = {}
    header = ET.parse(str(eaf_path)).getroot().find("HEADER")
    for p in header.iter("PROPERTY") if header is not None else []:
        name = p.get("NAME") or ""
        if name.startswith("multidata:"):
            props[name.split(":", 1)[1]] = p.text or ""
    return props


def gold_meta_path(case_id, references_dir):
    return Path(references_dir) / f"{case_id}.gold.json"


def write_gold_meta(case_id, references_dir, meta):
    path = gold_meta_path(case_id, references_dir)
    path.write_text(json.dumps(meta, indent=2) + "\n")
    return path


def read_gold_meta(case_id, references_dir):
    """A gold reference's `.gold.json`: how pass 1 was made, the seed if
    any, standards version, span. A reference without one predates seeding
    and was made blind."""
    path = gold_meta_path(case_id, references_dir)
    if not path.exists():
        return {"case_id": str(case_id), "pass1_source": "blind", "meta_file": False}
    return json.loads(path.read_text())


if __name__ == "__main__":     # quick look at a transcript's speakers
    import sys

    for spk, s in speaker_overview(utterances(json.loads(Path(sys.argv[1]).read_text()))).items():
        print(spk, f"{s['talk_s']:.0f}s", s["utterances"], *s["examples"], sep="\n  ")
