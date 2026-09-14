#!/usr/bin/env python
"""Check an annotated .eaf before it is submitted.

    python submit_checks.py <file.eaf> [--span-start S --span-end E]

Exit 0 = fine to submit (warnings may still be printed).
Exit 1 = do not submit; the message says what to fix.

Two checks, deliberately. The annotator guide's §7 hand-off list has more items
than this, but the rest need a human ear -- these two are the ones a machine can
decide, so they are the ones a machine does.

  REFUSE  an annotation with no text. The guide already says an empty segment
          must be transcribed or deleted, because it becomes a phantom turn in
          the diarization reference.
  WARN    annotations outside the excerpt window, when the case has one. A
          warning and not a refusal: the window is guidance to the annotator,
          and .eaf-level excerpt slicing is deliberately unbuilt, so refusing
          here would enforce a boundary the pipeline itself doesn't.

Standard library only, on purpose -- this runs on the annotator VM, and a check
script that needs packages installed is a check script that stops working.
"""
import argparse
import sys
import xml.etree.ElementTree as ET

# NOTES is for the annotator, never transcript content, and it is excluded from
# both gold.txt and gold.rttm. It also carries the excerpt-window marker a kit
# puts there, so the window check must skip it or it would flag itself.
NOTES_TIER = "NOTES"


def read_annotations(eaf_path):
    """[(tier, start_ms, end_ms, text)] for every time-aligned annotation."""
    root = ET.parse(eaf_path).getroot()

    slots = {s.get("TIME_SLOT_ID"): int(s.get("TIME_VALUE", 0))
             for s in root.iter("TIME_SLOT")}

    out = []
    for tier in root.iter("TIER"):
        tier_id = tier.get("TIER_ID")
        for ann in tier.iter("ALIGNABLE_ANNOTATION"):
            value = ann.find("ANNOTATION_VALUE")
            text = (value.text or "") if value is not None else ""
            out.append((
                tier_id,
                slots.get(ann.get("TIME_SLOT_REF1"), 0),
                slots.get(ann.get("TIME_SLOT_REF2"), 0),
                text.strip(),
            ))
    return out


def ts(ms):
    return f"{ms // 60000:d}:{ms % 60000 / 1000:06.3f}"


def check(annotations, span=None):
    """(problems, warnings) -- both lists of human-readable strings."""
    problems, warnings = [], []

    for tier, start, end, text in annotations:
        if not text:
            problems.append(f"empty segment on {tier} at {ts(start)}-{ts(end)}")

    if span:
        start_ms, end_ms = int(span[0] * 1000), int(span[1] * 1000)
        outside = [(t, s, e) for t, s, e, _ in annotations
                   if t != NOTES_TIER and (e <= start_ms or s >= end_ms)]
        for tier, s, e in outside:
            warnings.append(f"{tier} annotation at {ts(s)}-{ts(e)} is outside "
                            f"the excerpt window {ts(start_ms)}-{ts(end_ms)}")

    return problems, warnings


def summarise(annotations):
    counts = {}
    for tier, _, _, _ in annotations:
        counts[tier] = counts.get(tier, 0) + 1
    if not counts:
        return "no annotations at all"
    return ", ".join(f"{tier} {n}" for tier, n in sorted(counts.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eaf")
    ap.add_argument("--span-start", type=float, default=None)
    ap.add_argument("--span-end", type=float, default=None)
    args = ap.parse_args()

    span = None
    if args.span_start is not None and args.span_end is not None:
        span = (args.span_start, args.span_end)

    annotations = read_annotations(args.eaf)
    problems, warnings = check(annotations, span=span)

    print(f"{args.eaf}: {summarise(annotations)}")

    def show(items, label):
        for item in items[:10]:
            print(f"  {label}: {item}")
        if len(items) > 10:
            print(f"  ...and {len(items) - 10} more {label.lower()}s")

    show(warnings, "warning")

    if problems:
        show(problems, "PROBLEM")
        print("\nNot submitted. Transcribe or delete the empty segments, "
              "then try again.")
        return 1

    print("\nChecks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
