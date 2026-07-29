"""Stage: elan — build the .eaf annotation draft from a transcript (doc §7).

Promoted from `notebooks/transcription_suite.ipynb` cell 5 (the evolved version,
with speaker-name normalization) of the old multidata repo. Runs in **md-speech**.

Granularity is **word-level**: one annotation per word, one tier per speaker.
Note this differs from doc §7's "start utterance-level" suggestion — the working
notebook code is word-level, so that is what was promoted. Revisit if annotators
find word-level tiers unwieldy in ELAN.

The generated .eaf is the *machine draft*. Save human-corrected versions under a
distinct name so a re-run never overwrites hand-labels.
"""
import json
import logging
import re

import pympi

log = logging.getLogger(__name__)

# Bare integer speaker ids (e.g. "0") come back from some engines; ELAN tiers
# read much better as SPEAKER_00.
_BARE_INT = re.compile(r"^\d+$")

_ORPHAN_PUNCT = [".", ",", "?", "!"]


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


def build_eaf(transcription_data, mp4_path, output_eaf_path):
    """Write an .eaf with one tier per speaker and one annotation per word.

    :param transcription_data: dict, loaded JSON transcription (or a path to it)
    :param mp4_path: str, path to source video — link the video, not just audio,
        so annotators see gesture and speech together
    :param output_eaf_path: str, path to output .eaf file
    """
    if isinstance(transcription_data, (str, bytes)) or hasattr(transcription_data, "__fspath__"):
        with open(transcription_data) as f:
            transcription_data = json.load(f)

    eaf = pympi.Elan.Eaf()
    eaf.add_linked_file(file_path=str(mp4_path), mimetype="video/mp4")

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
