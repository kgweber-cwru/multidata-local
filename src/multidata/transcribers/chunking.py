"""Long audio for vendors with a length cap, and our own speaker labels for any
vendor.

**Chunking.** An adapter whose `Params` has `chunk_s` gets audio longer than
that sent in pieces, planned from the diarize stage's turns
(`data/diarization/<recording>/<camera>.rttm`; run the `audio` and `diarize`
stages first):

1. The audio is cut into pieces of at most `chunk_s` that overlap by
   `chunk_overlap_s`. Each overlap goes where, in the second half of the
   piece, the conversation changes hands most often, so it is likely to hold
   every speaker rather than a silence or a monologue such as instructions
   during a physical exam. (On our recordings that is a median of 17 turn
   changes per overlap; about 1 in 11 overlaps has none to find.) Turns
   shorter than `MIN_TURN_S` don't count: the diarize stage sometimes
   flickers between two labels in near-zero-length turns, and on 252848 that
   drew an overlap into a stretch of near silence.
2. `merge` puts the pieces back on the recording's timeline. In each overlap,
   words starting before its midpoint come from the earlier piece and the
   rest from the later one, unless one piece heard at least
   `RICHER_FACTOR` times as many words there (and `RICHER_MIN_EXTRA` more):
   then the whole overlap comes from that piece. A vendor can under-hear the
   last stretch of a piece; on 252848 one piece heard 7 words where the next
   heard 43.
3. Each piece is diarized by the vendor on its own. Its labels are linked to
   the previous piece's by the words both pieces heard in the overlap: labels
   that share at least `MIN_SHARED_WORDS` words, and mostly each other, are
   the same person.
4. A label the overlap can't link (its speaker was quiet there, or the
   overlap fell in a silence) is linked through the diarize stage's turns,
   which label the whole recording consistently: if at least
   `BRIDGE_MIN_SHARE` of its speech, and `BRIDGE_MIN_S` seconds, falls in one
   diarize speaker's turns, it joins the earlier label that maps to that same
   diarize speaker. A label still without a match stays a separate speaker,
   and the run's notes say so. Labels within one piece are never joined: how
   a vendor splits speakers is part of what is being compared.

Silences are sent, not skipped. Across 57 recordings the diarize stage finds
about five silences of 1-5 minutes per recording, not just the blanks around
the encounter, and it can miss quiet speech; skipping by its say-so would
drop words and split conversations (checked 2026-11-16).

The diarize stage's turns are used only to decide where to cut and to link
labels across pieces, never to label speakers, unless `speakers="pipeline"` asks for that: it gives each
word the speaker of the diarize turn it overlaps most, replacing the
vendor's labels. That is the control arm for comparing vendors' diarization
(spec §5, plan 4.5).
"""
import re
import subprocess
from collections import Counter, defaultdict

from multidata.paths import DATA_DIR

from .base import SpeakerLabels, segments_from_words

STEP_S = 10.0
MIN_SHARED_WORDS = 5
MATCH_TOLERANCE_S = 0.5
MIN_TURN_S = 0.3
RICHER_FACTOR = 2
RICHER_MIN_EXTRA = 5
BRIDGE_MIN_S = 5.0
BRIDGE_MIN_SHARE = 0.6


def pipeline_rttm(recording_id, camera):
    return DATA_DIR / "diarization" / recording_id / f"{camera}.rttm"


# --- planning ----------------------------------------------------------------

def turn_changes(turns, start, end):
    """How often the speaker changes between turns beginning in [start, end)."""
    labels = [label for s, e, label in sorted(turns)
              if start <= s < end and e - s >= MIN_TURN_S]
    return sum(a != b for a, b in zip(labels, labels[1:]))


def plan(turns, total_s, chunk_s, overlap_s):
    """[(start_s, end_s)] pieces of at most `chunk_s` seconds covering
    [0, total_s] (see the module docstring)."""
    if not 0 <= overlap_s < chunk_s / 2:
        raise ValueError("need 0 <= chunk_overlap_s < chunk_s / 2")
    pieces, start = [], 0.0
    while total_s - start > chunk_s:
        # The next piece starts at t; this one ends at t + overlap_s.
        latest = start + chunk_s - overlap_s
        earliest = start + chunk_s / 2
        candidates = []
        t = latest
        while t >= earliest:
            candidates.append(t)
            t -= STEP_S
        t = max(candidates, key=lambda c: turn_changes(turns, c, c + overlap_s))
        pieces.append((round(start, 3), round(t + overlap_s, 3)))
        start = t
    pieces.append((round(start, 3), round(total_s, 3)))
    return pieces


def cut(audio_path, out_dir, spans):
    """Mono 16 kHz FLAC pieces for `spans` [(start_s, end_s)]:
    [(path, offset_s, duration_s)]."""
    pieces = []
    for index, (start, end) in enumerate(spans):
        piece = out_dir / f"{index:02d}.flac"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{start:.3f}",
                        "-t", f"{end - start:.3f}", "-i", str(audio_path), "-ac", "1",
                        "-ar", "16000", str(piece)], check=True)
        pieces.append((piece, start, round(end - start, 3)))
    return pieces


# --- merging -----------------------------------------------------------------

def merge(pieces, turns=None):
    """[(offset_s, duration_s, normalized result)] -> (one normalized result on
    the recording's timeline, [notes]). `turns`, the diarize stage's
    [(start_s, end_s, speaker)], links labels the overlaps can't. See the
    module docstring."""
    timed = [[{**w, "start": _shift(w["start"], offset), "end": _shift(w["end"], offset),
               "speaker": w.get("speaker", seg.get("speaker"))}
              for seg in result["segments"] for w in seg.get("words", [])]
             for offset, _, result in pieces]

    # Piece-local label -> speaker id across pieces, linked through overlaps,
    # then through the diarize stage's turns.
    ids, notes, next_id = [], [], 0
    id_of = {}   # diarize speaker -> the speaker id first mapped to it
    for i, (offset, _, _) in enumerate(pieces):
        local = {w["speaker"] for w in timed[i]} - {None}
        links = {}
        if i and _overlap(pieces[i - 1], offset):
            links = link_labels(timed[i - 1], timed[i], offset, _end(pieces[i - 1]))
        bridged = bridge_labels(timed[i], turns) if turns else {}
        mapping = {label: ids[i - 1][links[label]] for label in local if label in links}
        claimed, via_turns, unlinked = set(mapping.values()), 0, 0
        for label in sorted(local - set(links)):
            target = id_of.get(bridged.get(label))
            if i and target is not None and target not in claimed:
                mapping[label] = target
                claimed.add(target)
                via_turns += 1
            else:
                mapping[label], next_id = next_id, next_id + 1
                unlinked += bool(i)
        for label, speaker in bridged.items():
            id_of.setdefault(speaker, mapping[label])
        if via_turns:
            notes.append(f"piece {i}: {via_turns} speaker label(s) linked through "
                         "the diarize stage's turns")
        if unlinked and (via_turns or _overlap(pieces[i - 1], offset)):
            notes.append(f"piece {i}: {unlinked} speaker label(s) not linked "
                         "to the previous piece")
        ids.append(mapping)

    # Where each piece hands over to the next.
    cuts = []
    for i in range(1, len(pieces)):
        cut, note = _handover(pieces[i - 1], pieces[i][0], timed[i - 1], timed[i])
        cuts.append(cut)
        if note:
            notes.append(f"piece {i}: {note}")

    speakers = SpeakerLabels()
    words, timing = [], True
    for i, (offset, _, result) in enumerate(pieces):
        lo = cuts[i - 1] if i else float("-inf")
        hi = cuts[i] if i + 1 < len(pieces) else float("inf")
        timing = timing and result.get("word_timing", True)
        keep = True   # an untimed word goes with the timed word before it
        for w in timed[i]:
            if w["start"] is not None:
                keep = lo <= w["start"] < hi
            if not keep:
                continue
            w = dict(w)
            label = w.pop("speaker")
            if label is not None:
                w["speaker"] = speakers(ids[i][label])
            words.append(w)
    merged = {"language": pieces[0][2]["language"], "segments": segments_from_words(words),
              "word_timing": timing}
    return merged, notes


def link_labels(earlier, later, start, end):
    """{later label: earlier label} for the speakers the two pieces' words in
    the overlap [start, end) show to be the same person."""
    def in_overlap(ws):
        return [w for w in ws if w["start"] is not None and w["speaker"] is not None
                and start <= w["start"] < end]

    pairs, used = Counter(), set()
    a_words = in_overlap(earlier)
    for b in in_overlap(later):
        for j, a in enumerate(a_words):
            if (j not in used and _norm(a["word"]) == _norm(b["word"])
                    and abs(a["start"] - b["start"]) <= MATCH_TOLERANCE_S):
                used.add(j)
                pairs[b["speaker"], a["speaker"]] += 1
                break

    totals = Counter()
    for (b_label, _), n in pairs.items():
        totals[b_label] += n
    links, taken = {}, set()
    for (b_label, a_label), n in pairs.most_common():
        if (b_label not in links and a_label not in taken
                and n >= MIN_SHARED_WORDS and n > totals[b_label] / 2):
            links[b_label] = a_label
            taken.add(a_label)
    return links


def bridge_labels(words, turns):
    """{vendor label: diarize speaker} for the labels whose timed speech falls
    mostly (`BRIDGE_MIN_SHARE`, and at least `BRIDGE_MIN_S` seconds) in one
    diarize speaker's turns."""
    seconds = defaultdict(Counter)
    for w in words:
        if w["start"] is None or w["end"] is None or w["speaker"] is None:
            continue
        end = max(w["end"], w["start"] + 0.01)
        for start, stop, speaker in turns:
            shared = min(stop, end) - max(start, w["start"])
            if shared > 0:
                seconds[w["speaker"]][speaker] += shared
    bridged = {}
    for label, by_speaker in seconds.items():
        total = sum(by_speaker.values())
        speaker, n = by_speaker.most_common(1)[0]
        if total >= BRIDGE_MIN_S and n / total >= BRIDGE_MIN_SHARE:
            bridged[label] = speaker
    return bridged


def _shift(t, offset):
    return None if t is None else round(t + offset, 3)


def _end(piece):
    return piece[0] + piece[1]


def _overlap(earlier, later_offset):
    return _end(earlier) > later_offset


def _handover(earlier, later_offset, earlier_words, later_words):
    """(time, note): where the merge switches from the earlier piece to the
    later one -- the overlap's midpoint, or an end of the overlap when one
    piece heard far more of it (see the module docstring)."""
    start, end = later_offset, _end(earlier)
    if end <= start:
        return _midpoint(earlier, later_offset), None
    a = sum(1 for w in earlier_words if w["start"] is not None and start <= w["start"] < end)
    b = sum(1 for w in later_words if w["start"] is not None and start <= w["start"] < end)
    if b >= RICHER_FACTOR * a and b - a >= RICHER_MIN_EXTRA:
        return start, f"overlap taken from this piece ({b} words against {a})"
    if a >= RICHER_FACTOR * b and a - b >= RICHER_MIN_EXTRA:
        return end, f"overlap taken from the previous piece ({a} words against {b})"
    return _midpoint(earlier, later_offset), None


def _midpoint(earlier, later_offset):
    """The middle of the overlap (or gap) between a piece and the next."""
    return (later_offset + _end(earlier)) / 2


def _norm(text):
    return re.sub(r"[^\w']", "", text.lower())


# --- pipeline speakers -------------------------------------------------------

def assign_speakers(result, turns):
    """Relabel every timed word with the speaker of the turn
    [(start_s, end_s, speaker)] it overlaps most (None if it overlaps none),
    and regroup the words into segments by speaker."""
    words = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            w = {k: v for k, v in w.items() if k != "speaker"}
            if w["start"] is not None and w["end"] is not None:
                best, speaker = 0.0, None
                w_end = max(w["end"], w["start"] + 0.01)   # zero-length words still land
                for start, end, label in turns:
                    overlap = min(end, w_end) - max(start, w["start"])
                    if overlap > best:
                        best, speaker = overlap, label
                if speaker is not None:
                    w["speaker"] = speaker
            words.append(w)
    return {**result, "segments": segments_from_words(words)}
