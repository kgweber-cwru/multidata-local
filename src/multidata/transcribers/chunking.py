"""Long audio for vendors with a length cap, and our own speaker labels for any
vendor.

**Chunking.** An adapter whose `Params` has `chunk_s` gets audio longer than
that cut into overlapping pieces (`cut`); `Transcriber.transcribe` sends each
piece and `merge` stitches the normalized results back onto the recording's
timeline. In each overlap, words starting before its midpoint come from the
earlier piece and the rest from the later one, so a word cut off at a piece's
edge is taken from the piece that heard it whole. Each piece is diarized on
its own, so its speakers get their own `SPEAKER_NN` labels: "speaker 1" in
two pieces may be different people, and nothing pretends otherwise.

**Pipeline speakers.** `speakers="pipeline"` replaces a vendor's labels with
the diarize stage's (`data/diarization/<recording>/<camera>.rttm`): each word
gets the speaker whose turn overlaps it most. Chunk boundaries then stop
mattering, and the labels match the pipeline's prosody and ELAN files. It is
also the control arm for comparing vendors' diarization (spec §5, plan 4.5).
"""
import subprocess

from multidata.paths import DATA_DIR

from .base import SpeakerLabels, segments_from_words


def cut(audio_path, out_dir, chunk_s, overlap_s):
    """Mono 16 kHz FLAC pieces of at most `chunk_s` seconds, each starting
    `overlap_s` before the previous one ends: [(path, offset_s, duration_s)]."""
    import soundfile

    if not 0 <= overlap_s < chunk_s:
        raise ValueError("need 0 <= overlap_s < chunk_s")
    total = soundfile.info(str(audio_path)).duration
    pieces, offset = [], 0.0
    while True:
        length = min(chunk_s, total - offset)
        piece = out_dir / f"{len(pieces):02d}.flac"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{offset:.3f}",
                        "-t", f"{length:.3f}", "-i", str(audio_path), "-ac", "1", "-ar", "16000",
                        str(piece)], check=True)
        pieces.append((piece, round(offset, 3), round(length, 3)))
        if offset + length >= total:
            return pieces
        offset += chunk_s - overlap_s


def merge(pieces):
    """[(offset_s, duration_s, normalized result)] -> one normalized result on
    the recording's timeline (see the module docstring)."""
    speakers = SpeakerLabels()
    words, timed = [], True
    for i, (offset, duration, result) in enumerate(pieces):
        lo = _midpoint(pieces[i - 1], offset) if i > 0 else float("-inf")
        hi = _midpoint((offset, duration), pieces[i + 1][0]) if i + 1 < len(pieces) else float("inf")
        timed = timed and result.get("word_timing", True)
        keep = True   # an untimed word goes with the timed word before it
        for seg in result["segments"]:
            for w in seg.get("words", []):
                start = None if w["start"] is None else round(w["start"] + offset, 3)
                if start is not None:
                    keep = lo <= start < hi
                if not keep:
                    continue
                label = w.get("speaker", seg.get("speaker"))
                words.append({**w, "start": start,
                              "end": None if w["end"] is None else round(w["end"] + offset, 3),
                              "speaker": speakers(f"{i}:{label}") if label else None})
    for w in words:
        if w["speaker"] is None:
            del w["speaker"]
    return {"language": pieces[0][2]["language"], "segments": segments_from_words(words),
            "word_timing": timed}


def _midpoint(earlier, later_offset):
    """The middle of the overlap between a piece (offset, duration, ...) and the next."""
    offset, duration = earlier[0], earlier[1]
    return (later_offset + offset + duration) / 2


def pipeline_rttm(recording_id, camera):
    return DATA_DIR / "diarization" / recording_id / f"{camera}.rttm"


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
