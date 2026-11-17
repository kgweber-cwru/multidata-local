#!/usr/bin/env python
"""Zip readable transcripts of every gold recording for the team: the human
gold transcript and each engine's transcript with its speakers, names
redacted.

    python benchmarks/make_transcript_share.py
    python benchmarks/make_transcript_share.py --out benchmarks/asr_transcripts.zip

One folder per recording, one plain-text file per transcript, in turns
("[0:01:23] Speaker 2: ..."), with a header giving that transcript's scores
from benchmarks/results/runs.csv. A README.txt explains the files for
readers who have not seen the benchmark report.

Names: each turn goes through `redact.redact_names`, the same redaction as
scoring; short forms of names can get through. The zip holds transcript
text, so it stays out of git (`benchmarks/*.zip`).
"""
import argparse
import csv
import datetime
import json
import re
import sys
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

import pympi  # noqa: E402

from multidata import manifest, redact  # noqa: E402
from multidata.elan import GOLD_TEXT_TIERS, _tier_annotations  # noqa: E402
from multidata.paths import DATA_DIR  # noqa: E402
from run_benchmark import REFERENCES, RESULTS, SCORING  # noqa: E402

# (engine, model, file label, name for readers), best pooled WER first.
# AssemblyAI's Medical Mode is left out: its words match plain AssemblyAI.
ENGINES = [
    ("assemblyai", "universal-3-5-pro", "AssemblyAI", "AssemblyAI universal-3-5-pro (cloud)"),
    ("gemini", "gemini-3.5-transcribe", "Gemini", "Google Gemini 3.5 Transcribe (cloud)"),
    ("elevenlabs", "scribe_v3", "ElevenLabs scribe_v3", "ElevenLabs scribe_v3 (cloud)"),
    ("elevenlabs", "scribe_v2_medical", "ElevenLabs scribe_v2_medical",
     "ElevenLabs scribe_v2_medical, the model sold with a BAA (cloud)"),
    ("google", "chirp_3", "Google Chirp 3", "Google Chirp 3 (cloud)"),
    ("faster_whisper", "medium", "faster-whisper", "faster-whisper medium (local)"),
    ("whisperx", "large-v3", "WhisperX", "WhisperX large-v3 (local)"),
    ("whisperx_disfluent", "large-v3", "WhisperX disfluent",
     "WhisperX large-v3, set to keep ums and uhs (local)"),
]

ROLE_LABELS = {"LEARNER": "Learner", "PATIENT": "Patient", "PRECEPTOR": "Preceptor",
               "ANNOUNCEMENT": "Announcement", "OUTSIDE_ROOM": "Outside the room"}

TURN_GAP_S = 2.0   # same speaker, pause up to this: one turn (for reading, not scoring)
WIDTH = 88

README = """\
Transcripts from the speech-to-text benchmark ({date})
=======================================================

These are the transcripts behind "ASR benchmark: first findings (November
2026)". They come from {n} simulated encounters in our sim center. Each
folder is one recording and holds:

  00 Human (gold).txt   what our annotators heard, transcribed by hand and
                        reviewed. The engines are scored against this.
  01 ... 08             what each engine produced from the same audio, best
                        overall score first.

The engines, in file order:
{engines}

How to read them
----------------
Each line is one turn: the time into the recording, who spoke, and what was
said. In the human transcript the speakers are named by role (Learner,
Patient, Preceptor, Announcement). The engines don't know who anyone is;
they only split the voices apart and number them, so "Speaker 1" in one
file is not the same person as "Speaker 1" in another. Working out which
number is the learner and which the patient is part of reading them.

Long silences (physical exam, note writing) show only as jumps in the time.

Each engine file starts with two scores for that recording:
  - Words wrong: the share of the human transcript's words the engine got
    wrong, left out, or added. 15% means roughly 1 word in 7.
  - Words wrong or with the wrong speaker: the same, but also counting words
    the engine gave to the wrong person.

Names
-----
Names have been replaced with tags such as [LEARNER_NAME], [PATIENT_NAME]
and [PRECEPTOR_NAME]. [PATIENT_NAME] stands for the actor's own name; the
character's name in the scenario is fictional and is left in. The
replacement is automatic, so the odd nickname or misspelled name gets
through.

Marks in the human transcript
-----------------------------
  um, uh           hesitations, written as said
  mm-hmm, uh-huh   "yes"; uh-uh and mm-mm mean "no"
  wor-             a word cut off part way
  so:              a drawn-out sound
  [unintelligible] nothing could be made out; [unintelligible: pressure?]
                   is the annotator's best guess
  [laughs] etc.    sounds other than words

Please keep these within the team: they record learners' performance.
"""


def redactor(names):
    def apply(text):
        return redact.redact_names(text, names)[0]
    return apply


def clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def merge(utterances):
    """[(start, end, speaker, text)] sorted by start -> turns: one speaker's
    consecutive utterances with pauses up to `TURN_GAP_S` joined."""
    turns = []
    for start, end, speaker, text in utterances:
        if turns and turns[-1][2] == speaker and start - turns[-1][1] <= TURN_GAP_S:
            s0, _, _, t0 = turns[-1]
            turns[-1] = (s0, max(end, turns[-1][1]), speaker, f"{t0} {text}")
        else:
            turns.append((start, end, speaker, text))
    return turns


def gold_turns(recording_id):
    eaf = pympi.Elan.Eaf(str(DATA_DIR / "gold" / recording_id / f"{recording_id}.gold.eaf"))
    utterances = [(s, e, ROLE_LABELS[tier], text) for tier in GOLD_TEXT_TIERS
                  for s, e, text in _tier_annotations(eaf, tier)]
    return merge(sorted(utterances))


def engine_turns(result):
    """Turns from a transcript's timed words, its speaker labels renumbered
    "Speaker 1, 2, ..." in order of first appearance."""
    numbers = {}
    utterances = []
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            text = (w.get("word") or "").strip()
            if not text or w.get("start") is None:
                continue
            label = w.get("speaker", seg.get("speaker"))
            if label is None:
                speaker = "Speaker ?"
            else:
                speaker = f"Speaker {numbers.setdefault(label, len(numbers) + 1)}"
            utterances.append((w["start"], w.get("end") or w["start"], speaker, text))
    return merge(utterances)


def render(header, turns, apply):
    lines = header + [""]
    for start, _, speaker, text in turns:
        lead = f"[{clock(start)}] {speaker}: "
        lines += textwrap.wrap(apply(text), WIDTH, initial_indent=lead,
                               subsequent_indent=" " * len(lead)) or [lead.rstrip()]
    return "\n".join(lines) + "\n"


def current_scores():
    """{(recording, engine, model): row} -- the latest plain (no variant) row
    scored with today's method."""
    out = {}
    with open(RESULTS, newline="") as f:
        for r in csv.DictReader(f):
            if r["scoring"] == SCORING and not r.get("setting"):
                key = (r["recording_id"], r["engine"], r["model"])
                if key not in out or r["timestamp"] > out[key]["timestamp"]:
                    out[key] = r
    return out


def percent(x):
    return f"{float(x):.0%}" if x not in (None, "") else "not scored"


def folder_name(recording_id, case_title):
    return f"{recording_id} - {re.sub(r'[:/]', ' -', case_title)}".strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    today = datetime.date.today()
    ap.add_argument("--out", default=str(ROOT / "benchmarks" / f"asr_transcripts_{today:%Y-%m}.zip"))
    args = ap.parse_args()

    recordings = sorted(p.name.removesuffix(".gold.txt") for p in REFERENCES.glob("*.gold.txt"))
    scores = current_scores()
    conn = manifest.connect(manifest.DEFAULT_PATH)
    titles = dict(conn.execute("SELECT recording_id, case_title FROM recordings"))
    conn.close()

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        engines = "\n".join(f"  {i:02d} {label:<30} {about}"
                            for i, (_, _, label, about) in enumerate(ENGINES, 1))
        z.writestr("README.txt", README.format(date=f"{today:%B %Y}", n=len(recordings),
                                               engines=engines))
        for recording_id in recordings:
            names = manifest.names_for_recording(recording_id)
            apply = redactor(names)
            title = titles.get(recording_id, "")
            folder = folder_name(recording_id, title)
            about = [f"Recording {recording_id}: {title}"]

            z.writestr(f"{folder}/00 Human (gold).txt", render(
                about + ["Human transcript (gold): the reference the engines are scored against."],
                gold_turns(recording_id), apply))

            transcripts = DATA_DIR / "transcripts" / recording_id
            for i, (engine, model, label, desc) in enumerate(ENGINES, 1):
                found = [p for p in transcripts.glob(f"*_{engine}_{model}.json")]
                if not found:
                    print(f"{recording_id}: no {engine} {model} transcript")
                    continue
                row = scores.get((recording_id, engine, model), {})
                header = about + [
                    f"Engine: {desc}",
                    f"Words wrong: {percent(row.get('wer'))}. "
                    f"Words wrong or with the wrong speaker: {percent(row.get('cpwer'))}.",
                ]
                result = json.loads(found[0].read_text())
                z.writestr(f"{folder}/{i:02d} {label}.txt",
                           render(header, engine_turns(result), apply))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
