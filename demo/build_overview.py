#!/usr/bin/env python
"""Build the "all encounters" data for the explorer: who talks when, in every case.

    python demo/build_overview.py

Standard library only, any env. Writes data/demo/overview.json from the
manifest, each case's diarization (data/diarization/) and pipeline transcript
(data/transcripts/). Everything here is machine output: speakers are
diarization labels (SPEAKER_00, ...), not roles, and diarization sometimes
merges two people (often learner and patient) or splits one.

The one role we can find reliably is the recorded announcements ("One minute
remaining"): the speaker most of whose words match stock phrases. Measures
leave it out.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from multidata import seeding  # noqa: E402  (standard library only)

DATA = ROOT / "data"
ANNOUNCE = re.compile(r"minutes? remaining|encounter begins|seconds until|please exit|"
                      r"you may begin|log ?in(to)? learning ?space|door note|"
                      r"time is up|feedback is over|minutes to (prepare|present)|"
                      r"encounter is over", re.I)
MIN_SPEAKER_S = 10.0


def read_rttm(path):
    out = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            out.append((float(p[3]), float(p[3]) + float(p[4]), p[7]))
    return sorted(out)


def union(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def total(iv):
    return sum(b - a for a, b in iv)


def measures(turns, announcer, duration):
    talk = [(a, b, s) for a, b, s in turns if s != announcer]
    if not talk:
        return {}
    speech = union([(a, b) for a, b, _ in talk])
    # overlap: time covered by 2+ speakers
    edges = sorted([(a, 1) for a, _, _ in talk] + [(b, -1) for _, b, _ in talk])
    depth, last, over = 0, 0.0, 0.0
    for t, d in edges:
        if depth >= 2:
            over += t - last
        depth += d
        last = t
    per = {}
    for a, b, s in talk:
        per[s] = per.get(s, 0.0) + b - a
    active = [s for s, v in per.items() if v >= MIN_SPEAKER_S]
    gaps = [b[0] - a[1] for a, b in zip(speech, speech[1:])]
    changes, prev = 0, None
    for a, b, s in talk:
        if s != prev and prev is not None:
            changes += 1
        prev = s
    span = speech[-1][1] - speech[0][0]
    top = max(per.values())
    return {
        "speech_pct": round(100 * total(speech) / duration, 1),
        "overlap_pct": round(100 * over / max(total(speech), 1), 1),
        "speakers": len(active),
        "longest_silence_s": round(max(gaps), 1) if gaps else 0.0,
        "turns_per_min": round(changes / max(span / 60, 1e-6), 1),
        "top_speaker_pct": round(100 * top / max(sum(per.values()), 1), 1),
    }


def human_status(case):
    gold = DATA / "gold" / case / f"{case}.gold.eaf"
    if gold.exists():
        return "gold"
    kinds = set()
    for kit in (ROOT / "annotation" / "kits" / case).glob("*/kit.json"):
        k = json.loads(kit.read_text())
        if k.get("pass1_source") == "seeded":
            kinds.add("seeded")
            continue
        eaf = kit.parent / k["eaf"]
        # an assigned kit starts empty: count it only once someone has annotated
        if eaf.exists() and len(re.findall(r"<ANNOTATION_VALUE>[^<\s]", eaf.read_text())) > 20:
            kinds.add("pass1")
    return "pass1" if "pass1" in kinds else ("seeded" if kinds else "")


def main():
    db = sqlite3.connect(f"file:{ROOT / 'manifest.sqlite'}?mode=ro", uri=True)
    rows = db.execute("SELECT c.case_id, c.room_number, c.audio_camera, v.duration_s "
                      "FROM cases c JOIN videos v ON v.case_id = c.case_id AND v.camera = c.audio_camera "
                      "ORDER BY c.room_number, c.case_id").fetchall()
    built = DATA / "demo" / "cases" / "index.json"
    built = json.loads(built.read_text()) if built.exists() else {}
    cases = []
    for case, room, cam, duration in rows:
        rttm = DATA / "diarization" / case / f"{cam}.rttm"
        tr = seeding.seed_transcript_path(case, cam, DATA)
        if not rttm.exists() or not tr.exists():
            print(f"skip {case}: no diarization or transcript")
            continue
        turns = read_rttm(rttm)
        utts = seeding.utterances(json.loads(tr.read_text()))
        hits, words = {}, {}
        for spk, a, b, text in utts:
            words[spk] = words.get(spk, 0) + len(text.split())
            if ANNOUNCE.search(text):
                hits[spk] = hits.get(spk, 0) + len(text.split())
        announcer = max(hits, key=lambda s: hits[s] / max(words[s], 1), default=None)
        if announcer and hits[announcer] < 0.3 * words[announcer]:
            announcer = None
        talk_time = {}
        for a, b, s in turns:
            talk_time[s] = talk_time.get(s, 0) + b - a
        order = sorted((s for s in talk_time if s != announcer), key=lambda s: -talk_time[s])
        spk_ix = {s: i for i, s in enumerate(order)}
        spk_ix[announcer] = -1
        cases.append({
            "case": case, "room": room, "camera": cam, "duration_s": round(duration, 1),
            "cohort": "2025" if case < "260000" else "2026",
            "human": human_status(case), "explorer": case in built,
            "speakers": order,
            "turns": [[round(a, 2), round(b - a, 2), spk_ix.get(s, -2)] for a, b, s in turns],
            "transcript": [[round(a, 1), spk_ix.get(s, -2), t] for s, a, b, t in utts],
            "measures": measures(turns, announcer, duration),
        })
    out = DATA / "demo" / "overview.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"cases": cases}, separators=(",", ":")))
    print(f"wrote {out}: {len(cases)} cases, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
