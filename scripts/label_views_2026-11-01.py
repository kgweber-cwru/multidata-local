#!/usr/bin/env python
"""One-off: provisional preset labels for the recurring camera views
(2026-11-01), matched by eye from contact sheets of each view and of the
view pairs the two cameras hold at the same time. Confidence in each note.
Drew/Kate confirm or correct with manifest.set_view_label(..., "confirmed").

    python scripts/label_views_2026-11-01.py          # any env
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from multidata import manifest  # noqa: E402

B, C, BD, S, PC = ("Baseline", "Chair (End of Exam Bed)", "Bed (Full Length of Exam Bed)",
                   "Sink", "PC")
# camera, view, label, confidence, why
LABELS = [
    # room 01
    ("337", "v1", B, "medium", "bed-side wide, desk and bed"),
    ("337", "v2", BD, "medium", "tighter on the reclined bed"),
    ("338", "v1", B, "high", "sink-side wide"),
    ("338", "v2", S, "low", "counter with someone at it; 1% of time"),
    # room 02
    ("339", "v2", BD, "medium", "reclined bed fills the frame"),
    ("339", "v6", C, "high", "tight on the end of the bed, learner seated there"),
    ("339", "v7", PC, "high", "desk zoom; paired with 37 v11"),
    ("37", "v1", B, "high", "sink-side wide; held during Bed and Chair too"),
    ("37", "v11", PC, "high", "desk zoom; paired with 339 v7"),
    ("37", "v2", S, "low", "counter with someone at it"),
    # room 03
    ("291", "v1", BD, "medium", "reclined bed fills the frame"),
    ("291", "v2", C, "low", "end of the bed with someone seated"),
    ("39", "v1", B, "high", "sink-side wide"),
    ("39", "v11", S, "medium", "counter-centred, someone standing at it"),
    # room 04
    ("12", "v5", B, "high", "sink-side wide"),
    ("12", "v8", PC, "high", "desk zoom; paired with 36 v5"),
    ("36", "v6", BD, "medium", "reclined bed fills the frame; paired with 12 v5"),
    ("36", "v5", PC, "high", "desk zoom; paired with 12 v8"),
    # room 05
    ("17", "v4", B, "high", "sink-side wide"),
    ("17", "v8", PC, "high", "desk zoom; paired with 34 v5"),
    ("17", "v7", BD, "medium", "sink-side camera zoomed onto the bed during an exam"),
    ("17", "v12", BD, "low", "as v7, tighter"),
    ("34", "v4", B, "medium", "bed-side, wider than v7, desk in frame"),
    ("34", "v7", BD, "medium", "reclined bed fills the frame; paired with 17 v4"),
    ("34", "v5", PC, "high", "desk zoom; paired with 17 v8"),
    # room 06
    ("14", "v1", B, "high", "bed-side wide; paired with 4 v1"),
    ("14", "v4", BD, "medium", "reclined bed; paired with 4 v5 (4 recordings)"),
    ("14", "v8", PC, "high", "desk zoom; paired with 4 v14"),
    ("4", "v1", B, "high", "sink-side wide; paired with 14 v1"),
    ("4", "v5", BD, "medium", "sink-side, near-wide; recalled with 14 v4 (Bed)"),
    ("4", "v14", PC, "high", "desk zoom; paired with 14 v8"),
    ("4", "v7", S, "medium", "counter-centred, someone standing at it"),
    # room 07
    ("24", "v2", B, "high", "sink-side wide"),
    ("33", "v2", B, "high", "bed-side wide, chair upright"),
    ("33", "v3", PC, "medium", "desk with person, chair edge in frame"),
    # room 08
    ("19", "v1", PC, "medium", "desk-centred with learner; paired with 32 v1"),
    ("32", "v1", PC, "medium", "desk-centred; paired with 19 v1"),
    ("32", "v2", B, "medium", "bed-side, wider: desk and chair"),
    # room 09
    ("23", "v1", B, "medium", "bed-side, chair with patient, desk in frame"),
    ("23", "v2", C, "high", "tight on the upright chair"),
    ("26", "v1", B, "high", "sink-side wide; held during Chair too"),
    # room 10
    ("10", "v3", B, "medium", "bed-side: door, chair, desk corner"),
    ("10", "v2", C, "medium", "tight on patient and learner at the chair"),
    ("41", "v2", B, "high", "sink-side wide"),
    # room 11
    ("16", "v1", B, "high", "sink-side wide; 97% of time"),
    ("31", "v1", B, "high", "bed-side wide"),
    # room 12
    ("293", "v1", PC, "medium", "desk with learner, chair in frame; paired with 42 v1"),
    ("293", "v4", B, "medium", "wider: door, desk; paired with 42 v10"),
    ("42", "v1", PC, "medium", "desk-centred; paired with 293 v1"),
    ("42", "v10", B, "medium", "sink-side wide with someone in it; paired with 293 v4"),
    ("42", "v11", B, "medium", "sink-side wide, empty"),
    ("42", "v4", S, "low", "counter and door, someone at the counter"),
    # room 13
    ("20", "v1", B, "high", "sink-side wide"),
    ("20", "v2", BD, "high", "sink-side camera top-down on the bed, full length"),
    ("20", "v5", BD, "medium", "as v2, tighter"),
    ("290", "v2", B, "high", "bed-side wide"),
    ("290", "v1", C, "high", "tight on the upright chair; paired with 20 v2"),
    # room 14
    ("13", "v1", B, "high", "bed-side wide"),
    ("9", "v1", B, "high", "sink-side wide"),
    ("9", "v3", C, "low", "sink-side camera tight on the chair"),
    # room 15
    ("22", "v1", B, "high", "sink-side wide; 87% of time"),
    ("308", "v1", B, "high", "bed-side wide"),
]
# recurring but no clear preset match (left unlabelled, noted)
UNMATCHED = [
    ("39", "v12", "tight on the bed from the sink side during an exam"),
    ("34", "v8", "bed from an odd angle, person at desk"),
    ("308", "v2", "tight on two people seated by the wall; maybe the operator's User preset"),
]

if __name__ == "__main__":
    assert all(lab in manifest.PRESETS for _, _, lab, _, _ in LABELS)
    for cam, vid, lab, conf, why in LABELS:
        manifest.set_view_label(cam, vid, lab, "provisional",
                                f"Claude visual match 2026-11-01, {conf} confidence: {why}")
    for cam, vid, why in UNMATCHED:
        manifest.set_view_label(cam, vid, "", "", f"recurring, no clear preset match: {why}")
    rec = {(r["camera"], r["view_id"]) for r in manifest.view_summary() if r["is_preset"]}
    done = {(c, v) for c, v, *_ in LABELS} | {(c, v) for c, v, _ in UNMATCHED}
    print(f"labelled {len(LABELS)}, unmatched {len(UNMATCHED)}; "
          f"recurring views not covered: {sorted(rec - done)}; "
          f"covered but not recurring: {sorted(done - rec)}")
