#!/usr/bin/env python
"""Score saved transcripts against gold (step 2 of 2; `transcribe.py` makes
the transcripts).

    python benchmarks/run_benchmark.py                        # every saved transcript of every gold recording
    python benchmarks/run_benchmark.py --recordings 261505
    python benchmarks/run_benchmark.py --transcript data/transcripts/261505/16_assemblyai_universal-3-5-pro.json

Looks in `data/transcripts/<recording>/` and its `variants/<setting>/`
folders, and appends one row per transcript to benchmarks/results/runs.csv,
the tracked ledger. A transcript already scored with today's method against
today's gold file is skipped (`--rescore` scores it again). `python
scripts/bench_status.py` shows the current numbers.

Scoring (`score()`): the manifest's names for the recording are redacted from
BOTH reference and hypothesis (`multidata.redact`), then both are normalized
with `multidata.normalize` -- L0 verbatim for `wer`/`cer`, L1 filler-neutral
for `wer_l1`. Bracket tags are deleted and numbers canonicalized at both
levels. Each row also keeps the counts behind the rates (gold words;
substitutions, deletions, insertions at L0; errors at L1) and how many
fillers and backchannels the transcript kept, so `scripts/bench_status.py`
can pool recordings exactly. Excerpt gold (a `span` in the `.gold.json`) is scored on that span
only. A transcript from the engine that seeded a recording's gold is never
scored against it (standards §9).

Speakers (`speaker_scores()`, transcription_standards.md §11): the
transcript's words become turns (same speaker, gaps up to `TURN_GAP_S`, the
rule gold's turns were built with) and are compared with `.gold.rttm`:
`der` and `jer` (pyannote.metrics, ±`COLLAR_S` around each gold boundary not
scored, overlap scored, every gold role a speaker), and `cpwer`, WER with
each engine speaker's words matched to one gold role's words (the gold
`.eaf` on this machine supplies the per-role text). `speech_s` (scored gold
speech time) and `cp_words`/`cp_errors` are kept for pooling.
"""
import argparse
import csv
import datetime
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import jiwer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest, normalize, redact, seeding  # noqa: E402
from multidata.elan import GOLD_TEXT_TIERS, _tier_annotations, words_of  # noqa: E402
from multidata.models import upgrade_keys  # noqa: E402
from multidata.paths import DATA_DIR  # noqa: E402

RESULTS = ROOT / "benchmarks" / "results" / "runs.csv"
REFERENCES = ROOT / "benchmarks" / "references"

# `source` is history: rows before 2026-11-16 say whether the engine ran live,
# came from the old response cache, or was a saved transcript; every row
# since is a saved transcript. `setting` is the settings.yaml name for a
# variant run ("" for a plain one); `transcript` is the file scored.
FIELDS = ["timestamp", "recording_id", "engine", "model", "audio_sha256",
          "wer", "cer", "wall_s", "source", "git_commit", "machine", "notes",
          "gold_pass1", "wer_l1", "scoring", "gold_sha256", "setting", "transcript",
          "der", "jer", "cpwer",
          "words", "sub", "del", "ins", "words_l1", "errors_l1",
          "speech_s", "cp_words", "cp_errors", "fillers", "backchannels"]

# Names the scoring method in each ledger row. Change it whenever score()
# changes in a way that moves numbers, so old and new rows aren't compared.
SCORING = "redact+L0/L1 v6"  # v2: markup off words at L0; L1 drops cut-off words. v3: ok -> okay. v4: numbers canonicalized. v5: + der/jer/cpwer. v6: + counts for pooling (rates as v5)

TURN_GAP_S = 0.3   # as gold's turns (elan.export_gold merge_gap)
MAX_WORD_S = 3.0   # no spoken word lasts longer; a vendor's word timed longer is a timing glitch
COLLAR_S = 0.5     # each side of a gold boundary: hand-placed boundaries are loose by up to ~0.5 s (DER levels off there; checked 2026-11-16)


def score(ref, hyp, names):
    """WER/CER at L0 and WER at L1, after redacting `names` from both sides,
    with the counts behind them and the hypothesis's filler and backchannel
    counts (L0 tokens in `normalize.FILLERS` / `BACKCHANNELS`).

    Redaction runs on the raw text (it matches names case-sensitively, before
    punctuation is gone); the resulting `[ROLE_NAME]` tags are then deleted
    by normalization like every other bracket tag, so a name counts neither
    for nor against an engine. `names` may be None (no manifest row): then
    nothing is redacted and the gold was exported unredacted too."""
    if names:
        ref, _ = redact.redact_names(ref, names)
        hyp, _ = redact.redact_names(hyp, names)
    ref0, hyp0 = normalize.normalize(ref, "l0"), normalize.normalize(hyp, "l0")
    ref1, hyp1 = normalize.normalize(ref, "l1"), normalize.normalize(hyp, "l1")
    o0, o1 = jiwer.process_words(ref0, hyp0), jiwer.process_words(ref1, hyp1)
    tokens = hyp0.split()
    return {
        "wer": round(o0.wer, 4),
        "cer": round(jiwer.cer(ref0, hyp0), 4),
        "wer_l1": round(o1.wer, 4),
        "words": o0.hits + o0.substitutions + o0.deletions,
        "sub": o0.substitutions, "del": o0.deletions, "ins": o0.insertions,
        "words_l1": o1.hits + o1.substitutions + o1.deletions,
        "errors_l1": o1.substitutions + o1.deletions + o1.insertions,
        "fillers": sum(t in normalize.FILLERS for t in tokens),
        "backchannels": sum(t in normalize.BACKCHANNELS for t in tokens),
    }


def hypothesis_text(result, span=None):
    """A transcript as plain text; with `span` `(start_s, end_s)`, only the
    words starting inside it (excerpt gold covers part of the audio, and
    words outside it would count as insertions). Filters by word when word
    timing exists, by segment otherwise."""
    segments = result.get("segments") or []
    if span is None:
        if segments and all("text" in s for s in segments):
            return " ".join(s["text"].strip() for s in segments)
        return " ".join(w.get("word", "").strip() for w in words_of(result))

    start, end = span
    words = words_of(result)
    if words:
        return " ".join(w.get("word", "").strip() for w in words
                        if start <= (w.get("start") or 0) < end)
    return " ".join(s["text"].strip() for s in segments if start <= s.get("start", 0) < end)


def speaker_turns(result, span=None):
    """[(start_s, end_s, speaker)]: the transcript's timed words with a
    speaker, merged into turns while the speaker stays the same and the gap
    is at most `TURN_GAP_S`. Bracketed audio-event tags ("[echoing]") are
    not speech, and a word timed longer than `MAX_WORD_S` is a vendor timing
    glitch (one ElevenLabs tag spanned 233 s, one Google word 164 s): both
    are left out, as WER already ignores the tags."""
    turns = []
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            speaker = w.get("speaker", seg.get("speaker"))
            start, end = w.get("start"), w.get("end")
            if speaker is None or start is None or end is None:
                continue
            if w.get("word", "").startswith("[") or end - start > MAX_WORD_S:
                continue
            if span and not span[0] <= start < span[1]:
                continue
            if turns and turns[-1][2] == speaker and start - turns[-1][1] <= TURN_GAP_S:
                turns[-1] = (turns[-1][0], max(turns[-1][1], end), speaker)
            else:
                turns.append((start, end, speaker))
    return turns


def read_rttm(path):
    turns = []
    for line in Path(path).read_text().splitlines():
        f = line.split()
        if f and f[0] == "SPEAKER":
            start = float(f[3])
            turns.append((start, start + float(f[4]), f[7]))
    return turns


def speaker_scores(ref_turns, hyp_turns, region):
    """`der` and `jer` of `hyp_turns` against `ref_turns` over `region`
    (start_s, end_s), and `speech_s`, the gold speech time DER is a share of."""
    from pyannote.core import Annotation, Segment, Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate, JaccardErrorRate

    def annotation(turns):
        a = Annotation()
        for i, (start, end, speaker) in enumerate(turns):
            if end > start:
                a[Segment(start, end), i] = speaker
        return a

    ref, hyp = annotation(ref_turns), annotation(hyp_turns)
    uem = Timeline([Segment(*region)])
    # pyannote's collar is the total width around a boundary
    try:
        der = DiarizationErrorRate(collar=2 * COLLAR_S, skip_overlap=False)(
            ref, hyp, uem=uem, detailed=True)
        jer = JaccardErrorRate(collar=2 * COLLAR_S, skip_overlap=False)(ref, hyp, uem=uem)
    except ZeroDivisionError:   # no reference speech left once the collars are removed
        return {"der": "", "jer": "", "speech_s": ""}
    return {"der": round(der["diarization error rate"], 4), "jer": round(jer, 4),
            "speech_s": round(der["total"], 2)}


def cpwer(ref_by_speaker, hyp_by_speaker):
    """WER over all speakers with each hypothesis speaker's words matched to
    one reference speaker's (the matching with fewest errors; an unmatched
    speaker on either side scores against nothing). Texts are already
    normalized."""
    errors, words = cpwer_counts(ref_by_speaker, hyp_by_speaker)
    return round(errors / words, 4) if words else None


def cpwer_counts(ref_by_speaker, hyp_by_speaker):
    """(errors, gold words) behind `cpwer()`."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    refs, hyps = list(ref_by_speaker.values()), list(hyp_by_speaker.values())
    n = max(len(refs), len(hyps))
    refs += [""] * (n - len(refs))
    hyps += [""] * (n - len(hyps))

    def errors(r, h):
        if not r:
            return len(h.split())
        if not h:
            return len(r.split())
        o = jiwer.process_words(r, h)
        return o.substitutions + o.deletions + o.insertions

    cost = np.array([[errors(r, h) for h in hyps] for r in refs])
    rows, cols = linear_sum_assignment(cost)
    return int(cost[rows, cols].sum()), sum(len(r.split()) for r in refs)


def gold_text_by_role(recording_id, names, span=None):
    """{role: L0-normalized gold text} from the gold `.eaf` (not in git; None
    if it isn't on this machine). Names are redacted as for WER."""
    import pympi

    eaf_path = DATA_DIR / "gold" / recording_id / f"{recording_id}.gold.eaf"
    if not eaf_path.exists():
        return None
    eaf = pympi.Elan.Eaf(str(eaf_path))
    by_role = {}
    for tier in GOLD_TEXT_TIERS:
        text = " ".join(t for s, _, t in _tier_annotations(eaf, tier)
                        if span is None or span[0] <= s < span[1])
        if names:
            text, _ = redact.redact_names(text, names)
        if text.strip():
            by_role[tier] = normalize.normalize(text, "l0")
    return by_role


def hypothesis_by_speaker(result, names, span=None):
    words = {}
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            speaker = w.get("speaker", seg.get("speaker"))
            start = w.get("start")
            if speaker is None or (span and (start is None or not span[0] <= start < span[1])):
                continue
            words.setdefault(speaker, []).append(w.get("word", "").strip())
    out = {}
    for speaker, ws in words.items():
        text = " ".join(ws)
        if names:
            text, _ = redact.redact_names(text, names)
        out[speaker] = normalize.normalize(text, "l0")
    return out


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def record(row, results_path=RESULTS):
    """Append one row to the ledger, writing the header on first use.
    `results_path` lets a test or a scratch run keep away from the real one."""
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    _upgrade_header(results_path)
    is_new = not results_path.exists()
    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _upgrade_header(results_path):
    """Rewrite an existing ledger whose header predates a column in FIELDS:
    same rows, the new column left empty. A `case_id` column (before the
    rename to LearningSpace's terms) becomes `recording_id`."""
    if not results_path.exists():
        return
    with open(results_path, newline="") as f:
        rows = [upgrade_keys(r) for r in csv.DictReader(f)]
        header = rows[0].keys() if rows else None
    if header is None or list(header) == FIELDS:
        return
    with open(results_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def seed_conflict(engine, meta):
    """Why `engine` may not be scored against this gold, or None: an engine
    that seeded a recording's gold would be scored in its own favour."""
    if meta.get("pass1_source") == "seeded" and engine == meta.get("seed_engine"):
        return (f"its gold was seeded from {engine} ({meta.get('seed_model')}); "
                "score it on blind gold or gold seeded by another engine (standards §9)")
    return None


def describe(path):
    """(engine, model, setting) of a saved transcript. Older local transcripts
    carry no provenance; their model comes from the file name
    (`<camera>_<engine>_<model>.json`)."""
    result = json.loads(Path(path).read_text())
    prov = result.get("provenance") or {}
    engine = prov.get("engine") or result.get("engine")
    if not engine:
        raise ValueError(f"{path} records no engine")
    model = prov.get("model") or Path(path).stem.split(f"_{engine}_", 1)[-1]
    parent = Path(path).parent
    setting = parent.name if parent.parent.name == "variants" else ""
    return result, engine, model, setting


def transcripts_of(recording_id):
    base = DATA_DIR / "transcripts" / recording_id
    found = list(base.glob("*.json")) + list(base.glob("variants/*/*.json"))
    return sorted(p for p in found if not p.name.endswith(".raw.json"))


def scored_already(results_path):
    """{(transcript, gold_sha256)} scored with today's method."""
    if not Path(results_path).exists():
        return set()
    with open(results_path, newline="") as f:
        return {(r.get("transcript"), r.get("gold_sha256")) for r in csv.DictReader(f)
                if r.get("scoring") == SCORING and r.get("transcript")}


def score_transcript(path, recording_id, results_path=RESULTS, manifest_path=manifest.DEFAULT_PATH,
                     references=REFERENCES, notes="", audio_hashes=None):
    """Score one saved transcript and append its ledger row; returns the row,
    or a string saying why it was not scored."""
    reference = Path(references) / f"{recording_id}.gold.txt"
    meta = seeding.read_gold_meta(recording_id, reference.parent)
    result, engine, model, setting = describe(path)
    conflict = seed_conflict(engine, meta)
    if conflict:
        return f"not scored: {conflict}"

    span = tuple(meta["span"]) if meta.get("span") else None
    hyp = hypothesis_text(result, span=span)
    try:
        names = manifest.names_for_recording(recording_id, manifest_path)
    except KeyError:
        names = None
        print(f"WARNING: no manifest row for {recording_id}: names not redacted before scoring",
              file=sys.stderr)
    scores = score(reference.read_text(), hyp, names)

    prov = result.get("provenance") or {}
    rttm = reference.with_name(f"{recording_id}.gold.rttm")
    hyp_turns = speaker_turns(result, span)
    if rttm.exists() and hyp_turns:
        ref_turns = read_rttm(rttm)
        end = prov.get("audio_duration_s") or max(e for _, e, _ in ref_turns + hyp_turns)
        scores |= speaker_scores(ref_turns, hyp_turns, span or (0.0, end))
        gold_roles = gold_text_by_role(recording_id, names, span)
        if gold_roles:
            errors, words = cpwer_counts(gold_roles, hypothesis_by_speaker(result, names, span))
            scores |= {"cpwer": round(errors / words, 4) if words else "",
                       "cp_words": words, "cp_errors": errors}
    audio = ROOT / prov["audio"] if prov.get("audio") else None
    if audio_hashes is not None and audio is not None and audio.exists():
        audio_sha = audio_hashes.setdefault(audio, sha256(audio))
    else:
        audio_sha = ""
    row = {
        "timestamp": datetime.datetime.now().isoformat(),
        "recording_id": recording_id, "engine": engine, "model": model,
        "audio_sha256": audio_sha, "wer": scores["wer"], "cer": scores["cer"],
        "wer_l1": scores["wer_l1"], "wall_s": prov.get("wall_s", ""),
        "source": "transcript", "git_commit": subprocess.getoutput("git rev-parse --short HEAD"),
        "machine": platform.platform(), "notes": notes,
        "gold_pass1": meta.get("pass1_source", "blind"), "scoring": SCORING,
        "gold_sha256": sha256(reference), "setting": setting,
        "transcript": _relative(path), "der": scores.get("der", ""),
        "jer": scores.get("jer", ""), "cpwer": scores.get("cpwer", ""),
    } | {k: scores.get(k, "") for k in FIELDS[FIELDS.index("words"):]}
    record(row, results_path=results_path)
    return row


def _relative(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recordings", nargs="+", help="default: every gold recording")
    ap.add_argument("--transcript", help="score this one file (its recording is in its path)")
    ap.add_argument("--rescore", action="store_true", help="score again even if already current")
    ap.add_argument("--notes", default="")
    ap.add_argument("--results", default=str(RESULTS),
                    help="the ledger; point a scratch run somewhere else")
    args = ap.parse_args()

    if args.transcript:
        path = Path(args.transcript).resolve()
        recording_id = next(p for p in path.parents if p.parent.name == "transcripts").name
        jobs = [(recording_id, path)]
    else:
        recordings = args.recordings or sorted(
            p.name.removesuffix(".gold.txt") for p in REFERENCES.glob("*.gold.txt"))
        jobs = [(r, p) for r in recordings for p in transcripts_of(r)]

    done = set() if args.rescore else scored_already(args.results)
    audio_hashes = {}
    for recording_id, path in jobs:
        reference = REFERENCES / f"{recording_id}.gold.txt"
        if not reference.exists():
            print(f"{recording_id}: no gold at {reference}")
            continue
        if (_relative(path), sha256(reference)) in done:
            continue
        out = score_transcript(path, recording_id, results_path=args.results,
                               notes=args.notes, audio_hashes=audio_hashes)
        label = _relative(path).removeprefix(f"data/transcripts/{recording_id}/")
        if isinstance(out, str):
            print(f"{recording_id:<8} {label:<62} {out}")
        else:
            speakers = (f"  DER {out['der']:.3f}  JER {out['jer']:.3f}  cpWER {out['cpwer']}"
                        if out["der"] != "" else "  (no speakers)")
            print(f"{recording_id:<8} {label:<62} WER {out['wer']:.3f} (L1 {out['wer_l1']:.3f})"
                  f"{speakers}")
    print(f"ledger: {args.results}")


if __name__ == "__main__":
    sys.exit(main())
