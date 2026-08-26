"""Stage: elan — build the .eaf annotation draft from a transcript (doc §7).

Promoted from `notebooks/transcription_suite.ipynb` cell 5 (the evolved version,
with speaker-name normalization) of the old multidata repo. Runs in **md-speech**.

Granularity is **word-level**: one annotation per word, one tier per speaker.
That's what the working notebook produced, and it no longer needs to match the
gold workflow — gold is utterance-level and hand-segmented (see below).

The generated .eaf is the *machine draft*, and since 2026-08-26 it is **not an
annotation starting point**. Gold references are produced blind and from
scratch against `elan/template.etf`, because correcting a Whisper draft biases
the reference toward Whisper in a way that can't be detected afterward
(docs/transcription_standards.md §9). This draft's remaining jobs are to be a
browsable artifact for the production pipeline and the comparison document for
pass-2 adjudication.

Never write a draft over hand-labelled work: gold lives under
`data/gold/<case_id>/` with distinct names and is treated as immutable.
"""
import json
import logging
import re
from pathlib import Path

import pympi

log = logging.getLogger(__name__)

# Bare integer speaker ids (e.g. "0") come back from some engines; ELAN tiers
# read much better as SPEAKER_00.
_BARE_INT = re.compile(r"^\d+$")

_ORPHAN_PUNCT = [".", ",", "?", "!"]

# Role tiers, pre-created empty on every draft, mirroring the naming used by
# the hand-annotation template (`elan/template.etf`) so a draft and a gold file
# can be read side by side in pass-2 adjudication
# (docs/transcription_standards.md §4/§9).
#
# NOTE: these no longer exist for anyone to *move* words into. Gold is now
# annotated blind and from scratch against the template, not by correcting this
# draft, so nothing reattributes the diarizer's raw SPEAKER_NN tiers here any
# more. They're kept for naming consistency and side-by-side reading; if that
# stops earning its keep, dropping them is safe. The template's NOTES tier is
# deliberately *not* mirrored — it's for human observations, and a machine
# draft has none.
DEFAULT_TIERS = ("LEARNER", "PATIENT", "PRECEPTOR", "ANNOUNCEMENT", "OUTSIDE_ROOM")


def format_speaker_name(speaker_name):
    if speaker_name == "Unknown_Speaker":
        return speaker_name
    if _BARE_INT.match(speaker_name):
        return f"SPEAKER_{int(speaker_name):02d}"
    return speaker_name


def words_of(transcription_data):
    """Prefer root 'words', fall back to words flattened from 'segments'."""
    words = transcription_data.get("words", [])
    if not words and "segments" in transcription_data:
        words = [
            w
            for segment in transcription_data["segments"]
            for w in segment.get("words", [])
        ]
    return words


def build_eaf(transcription_data, mp4_path, output_eaf_path, wav_path=None):
    """Write an .eaf with one tier per speaker and one annotation per word.

    :param transcription_data: dict, loaded JSON transcription (or a path to it)
    :param mp4_path: str, path to source video — link the video (not just audio),
        so annotators see gesture and speech together
    :param output_eaf_path: str, path to output .eaf file
    :param wav_path: str, optional path to the isolated audio -- linked
        *alongside* the video (not instead of it) so ELAN shows a waveform
        view without giving up the video. Skipped if not given.
    """
    if isinstance(transcription_data, (str, bytes)) or hasattr(transcription_data, "__fspath__"):
        with open(transcription_data) as f:
            transcription_data = json.load(f)

    eaf = pympi.Elan.Eaf()
    eaf.add_linked_file(file_path=str(mp4_path), mimetype="video/mp4")
    if wav_path is not None:
        eaf.add_linked_file(file_path=str(wav_path), mimetype="audio/x-wav")

    for tier in DEFAULT_TIERS:
        eaf.add_tier(tier)

    words = words_of(transcription_data)
    if not words:
        raise ValueError("No word-level data found in transcription.")

    added_count = 0
    for item in words:
        word_str = item.get("word", "").strip()
        if not word_str or word_str in _ORPHAN_PUNCT:
            continue

        speaker_name = format_speaker_name(item.get("speaker", "Unknown_Speaker"))
        start_ms = int(round(item["start"] * 1000))
        end_ms = int(round(item["end"] * 1000))

        if speaker_name not in eaf.get_tier_names():
            eaf.add_tier(speaker_name)

        # Zero-length annotations are rejected by ELAN
        if end_ms > start_ms:
            eaf.add_annotation(
                id_tier=speaker_name,
                start=start_ms,
                end=end_ms,
                value=word_str,
            )
            added_count += 1

    eaf.to_file(str(output_eaf_path))
    log.info("wrote %s  (%d words, tiers=%s)",
             output_eaf_path, added_count, list(eaf.get_tier_names()))
    return output_eaf_path


# Tier inclusion rules for gold export (docs/transcription_standards.md §4):
# clinical-encounter speech goes in both the transcript and the diarization
# reference; ANNOUNCEMENT/OUTSIDE_ROOM timing goes in the RTTM only (so a
# diarizer isn't falsely penalized via DER for correctly noticing a real
# extra voice) but their *text* is excluded -- the benchmark measures
# transcription of the encounter, not whether ASR caught a PA announcement or
# a hallway conversation. NOTES is in neither -- it's for humans, never a
# transcript.
GOLD_TEXT_TIERS = ("LEARNER", "PATIENT", "PRECEPTOR")
GOLD_RTTM_TIERS = ("LEARNER", "PATIENT", "PRECEPTOR", "ANNOUNCEMENT", "OUTSIDE_ROOM")


def _tier_annotations(eaf, tier):
    """[(start_s, end_s, text)] for one tier, sorted by start, skipping empty
    annotations. [] for a tier absent from this .eaf entirely -- not every
    case has ANNOUNCEMENT/OUTSIDE_ROOM content, and that's not an error."""
    if tier not in eaf.get_tier_names():
        return []
    spans = []
    for start_ms, end_ms, value in eaf.get_annotation_data_for_tier(tier):
        text = (value or "").strip()
        if text:
            spans.append((start_ms / 1000.0, end_ms / 1000.0, text))
    return sorted(spans, key=lambda span: span[0])


def _merge_adjacent(annotations, gap_s):
    """[(start,end,text)] sorted by start -> [(start,end)], merging whenever
    the gap to the previous annotation is <= gap_s -- turn-level RTTM built by
    merging adjacent same-tier utterance segments (transcription_standards.md
    §5). Only used for the RTTM; the transcript is built from the un-merged
    per-annotation text so word order stays exactly as segmented."""
    merged = []
    for start, end, _ in annotations:
        if merged and start - merged[-1][1] <= gap_s:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def export_gold(eaf_path, out_txt=None, out_rttm=None, names=None, merge_gap=0.3, uri=None):
    """The reverse of `build_eaf`: a hand-annotated .eaf -> the two gold
    reference formats `run_benchmark.py` (WER/CER) and `pyannote.metrics`
    (DER) consume (docs/transcription_standards.md §4/§11).

    Works on any .eaf with the standard role tiers -- a frozen `pass1.eaf` or
    an adjudicated `gold.eaf` alike; this function doesn't enforce which
    stage you're at, that's a human process decision (standards §9), not
    something an export tool should gatekeep.

    `names`, if given (see `manifest.names_for_case`), redacts the transcript
    via `multidata.redact.redact_names` before it's returned or written.
    **Pass `names=None` only when you have already decided the output will
    never reach a tracked or shared location** — under the current annotation
    policy (standards §8) every .eaf may contain real names, so skipping
    redaction is an explicit choice, not a default to fall into. The CLI
    wrapper (`scripts/eaf_to_gold.py`) enforces this; call this function
    directly and you're the one enforcing it.

    Same-tier annotations separated by <= `merge_gap` seconds merge into one
    RTTM turn (§5) — a readability/turn-shape convention, not a DER
    correctness requirement (DER scores time-labeled overlap, not turn
    count), so the default is a reasonable guess, not a tuned constant.

    Returns `(text, rttm_lines, substitutions)` — `substitutions` is `[]`
    when `names` is `None`. Writes to `out_txt`/`out_rttm` if given (parents
    created); `uri` (default: the .eaf's filename stem) is the RTTM's
    file-id field.
    """
    import pympi

    eaf = pympi.Elan.Eaf(str(eaf_path))

    text_spans = []
    for tier in GOLD_TEXT_TIERS:
        text_spans.extend(_tier_annotations(eaf, tier))
    text_spans.sort(key=lambda span: span[0])
    text = " ".join(t for _, _, t in text_spans)

    substitutions = []
    if names is not None:
        from multidata.redact import redact_names

        text, substitutions = redact_names(text, names)

    turns = []
    for tier in GOLD_RTTM_TIERS:
        for start, end in _merge_adjacent(_tier_annotations(eaf, tier), merge_gap):
            turns.append((start, end, tier))
    turns.sort(key=lambda turn: turn[0])

    uri = uri or Path(eaf_path).stem
    rttm_lines = [
        f"SPEAKER {uri} 1 {start:.3f} {end - start:.3f} <NA> <NA> {label} <NA> <NA>"
        for start, end, label in turns
    ]

    if out_txt is not None:
        out_txt = Path(out_txt)
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        out_txt.write_text(text + "\n" if text else "")

    if out_rttm is not None:
        out_rttm = Path(out_rttm)
        out_rttm.parent.mkdir(parents=True, exist_ok=True)
        out_rttm.write_text("\n".join(rttm_lines) + "\n" if rttm_lines else "")

    return text, rttm_lines, substitutions
