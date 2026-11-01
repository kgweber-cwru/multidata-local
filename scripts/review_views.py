#!/usr/bin/env python
"""Review camera views: contact sheets, and a spreadsheet to confirm labels.

    python scripts/review_views.py sheets            # md-pose env (needs OpenCV)
    python scripts/review_views.py export            # any env
    python scripts/review_views.py import            # any env; dry run
    python scripts/review_views.py import --write

The `views` stage gives every steady camera view an id (v1, v2, ... per
camera) and keeps sample frames of it in data/views/library/. People name the
views after the operators' presets (manifest.PRESETS). This script is the
loop for doing that:

1. `sheets` redraws data/views/sheets/ from the current library and manifest:
     recurring_<n>.png   every recurring view (seen in 2+ cases or held 3+
                         times), one tile each, tagged
                         "R<room> c<camera> v<n> <share>% <label>"
     pairs_room<NN>.png  view pairs the room's two cameras hold at the same
                         time, with minutes and cases: a preset moves both
     Old sheets are deleted first, so what's there is always current.
2. `export` writes data/views/review/labels.csv: one row per recurring or
   labelled view (--all: every view), with its current label, how sure the
   label is (notes), the sample strip to look at, and two empty columns:
   `confirmed_label` and `confirm_notes`.
3. You fill in `confirmed_label` for the rows you've checked: a preset name
   exactly as in manifest.PRESETS, or `none` for "not a preset". Leave it
   blank to skip a row.
4. `import` reads it back and shows what would change; `--write` saves
   each filled row as a confirmed label (manifest.set_view_label).
"""
import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from multidata import manifest  # noqa: E402
from multidata.paths import DATA_DIR, MANIFEST_PATH  # noqa: E402

VIEWS = DATA_DIR / "views"
SHEETS = VIEWS / "sheets"
REVIEW_CSV = VIEWS / "review" / "labels.csv"
SHORT = {"Baseline": "Baseline", "Chair (End of Exam Bed)": "Chair",
         "Bed (Full Length of Exam Bed)": "Bed", "Sink": "Sink", "PC": "PC", "User": "User"}


def _rooms(db):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    return dict(c.execute("SELECT camera_id, room_number FROM cameras"))


def _summary(db):
    rooms = _rooms(db)
    rows = manifest.view_summary(db)
    total = defaultdict(float)
    for r in rows:
        total[r["camera"]] += r["seconds"]
    for r in rows:
        r["room"] = rooms.get(r["camera"], "?")
        r["share_pct"] = round(100 * r["seconds"] / total[r["camera"]], 1) if total[r["camera"]] else 0.0
    return rows


def _room_key(room):
    return (0, int(room)) if str(room).isdigit() else (1, str(room))


def _tile(cam, view_id, text):
    import cv2
    import numpy as np

    with np.load(VIEWS / "library" / str(cam) / f"{view_id}.npz") as z:
        im = cv2.cvtColor(z["frames"][len(z["frames"]) // 2], cv2.COLOR_GRAY2BGR)
    cv2.rectangle(im, (0, 0), (im.shape[1] - 1, 24), (0, 0, 0), -1)
    cv2.putText(im, text, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    return im


def _grid(tiles, per_row):
    import numpy as np

    blank = np.zeros_like(tiles[0])
    tiles = tiles + [blank] * (-len(tiles) % per_row)
    return np.vstack([np.hstack(tiles[i:i + per_row]) for i in range(0, len(tiles), per_row)])


def cmd_sheets(db):
    import cv2

    SHEETS.mkdir(parents=True, exist_ok=True)
    for old in SHEETS.glob("*.png"):
        old.unlink()
    rows = [r for r in _summary(db) if r["is_preset"]]
    rows.sort(key=lambda r: (_room_key(r["room"]), int(r["camera"]), -r["seconds"]))
    tiles = [_tile(r["camera"], r["view_id"],
                   f"R{r['room']} c{r['camera']} {r['view_id']} {r['share_pct']:.0f}% "
                   f"{SHORT.get(r['label'], r['label'] or '-')}"
                   + ("" if r["label_source"] != "confirmed" else " *"))
             for r in rows]
    per_page = 24
    for p in range(0, len(tiles), per_page):
        cv2.imwrite(str(SHEETS / f"recurring_{p // per_page + 1}.png"),
                    _grid(tiles[p:p + per_page], 6))
    n_pairs = _pair_sheets(db)
    print(f"{len(rows)} recurring views on {-(-len(tiles) // per_page)} sheet(s); "
          f"pair sheets for {n_pairs} room(s) -> {SHEETS.relative_to(ROOT)}  "
          "(* = confirmed label)")


def _pair_sheets(db, min_s=180, min_cases=2):
    import cv2
    import numpy as np

    from multidata import views

    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    vids = c.execute("SELECT v.case_id, v.camera, cs.room_number FROM videos v "
                     "JOIN cases cs USING (case_id)").fetchall()
    by_case = defaultdict(dict)
    for case_id, cam, room in vids:
        p = VIEWS / case_id / f"{cam}.json"
        if p.exists():
            by_case[(room, case_id)][cam] = json.loads(p.read_text())["stretches"]
    pairs = defaultdict(lambda: [0.0, set()])
    for (room, case_id), cams in by_case.items():
        if len(cams) != 2:
            continue
        (ca, sa), (cb, sb) = sorted(cams.items(), key=lambda kv: int(kv[0]))
        for (va, vb), sec in views.pair_views(sa, sb).items():
            k = (room, ca, va, cb, vb)
            pairs[k][0] += sec
            pairs[k][1].add(case_id)
    labels = {(r["camera"], r["view_id"]): r["label"] for r in manifest.view_summary(db)}
    rooms = defaultdict(list)
    for k, (sec, cases) in pairs.items():
        if sec >= min_s and len(cases) >= min_cases:
            rooms[k[0]].append((k, sec, len(cases)))
    for room, lst in rooms.items():
        lst.sort(key=lambda x: -x[1])
        rows = []
        for (_, ca, va, cb, vb), sec, n in lst[:8]:
            side = np.zeros((180, 150, 3), np.uint8)
            for i, line in enumerate((f"{sec / 60:.0f} min", f"{n} cases")):
                cv2.putText(side, line, (8, 80 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (255, 255, 255), 1, cv2.LINE_AA)
            rows.append(np.hstack([side] + [
                _tile(cam, v, f"c{cam} {v} {SHORT.get(labels.get((cam, v)), labels.get((cam, v)) or '-')}")
                for cam, v in ((ca, va), (cb, vb))]))
        name = f"pairs_room{int(room):02d}.png" if str(room).isdigit() else f"pairs_room{room}.png"
        cv2.imwrite(str(SHEETS / name), np.vstack(rows))
    return len(rooms)


COLUMNS = ["room", "camera", "view_id", "share_pct", "cases", "stretches", "recurring",
           "label", "label_source", "notes", "sample", "confirmed_label", "confirm_notes"]


def cmd_export(db, include_all):
    rows = [r for r in _summary(db) if include_all or r["is_preset"] or r["label"]]
    rows.sort(key=lambda r: (_room_key(r["room"]), int(r["camera"]), -r["seconds"]))
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    notes = {(cam, v): n for cam, v, n in c.execute("SELECT camera, view_id, notes FROM camera_views")}
    REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    if REVIEW_CSV.exists():
        sys.exit(f"{REVIEW_CSV.relative_to(ROOT)} exists -- import or delete it first, "
                 "so filled-in answers aren't overwritten")
    with open(REVIEW_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r["room"], r["camera"], r["view_id"], r["share_pct"], r["cases"],
                        r["stretches"], "yes" if r["is_preset"] else "no", r["label"],
                        r["label_source"], notes.get((r["camera"], r["view_id"]), ""),
                        f"data/views/library/{r['camera']}/{r['view_id']}.png", "", ""])
    print(f"wrote {len(rows)} rows to {REVIEW_CSV.relative_to(ROOT)}; fill in confirmed_label "
          f"({' | '.join(manifest.PRESETS)} | none), then: review_views.py import")


def cmd_import(db, write):
    if not REVIEW_CSV.exists():
        sys.exit(f"no {REVIEW_CSV.relative_to(ROOT)} -- run export first")
    todo, bad = [], []
    with open(REVIEW_CSV, newline="") as f:
        for r in csv.DictReader(f):
            lab = (r["confirmed_label"] or "").strip()
            if not lab:
                continue
            if lab.lower() == "none":
                lab = ""
            elif lab not in manifest.PRESETS:
                bad.append((r["camera"], r["view_id"], lab))
                continue
            todo.append((r["camera"], r["view_id"], r["label"], lab, (r["confirm_notes"] or "").strip()))
    for cam, v, lab in bad:
        print(f"  SKIPPED c{cam} {v}: {lab!r} is not a preset name or 'none'")
    for cam, v, old, new, note in todo:
        print(f"  c{cam} {v}: {old or '-'} -> {new or '(not a preset)'}" + (f"  [{note}]" if note else ""))
        if write:
            manifest.set_view_label(cam, v, new, "confirmed", notes=note or "confirmed in review",
                                    path=db)
    print(f"{len(todo)} row(s) {'saved' if write else 'would be saved (dry run; add --write)'}"
          + (f", {len(bad)} skipped" if bad else ""))
    if write and todo:
        done = REVIEW_CSV.with_name("labels_imported.csv")
        REVIEW_CSV.replace(done)
        print(f"moved the sheet to {done.relative_to(ROOT)}; run sheets to redraw")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["sheets", "export", "import"])
    ap.add_argument("--all", action="store_true", help="export: every view, not just recurring")
    ap.add_argument("--write", action="store_true", help="import: save (default is a dry run)")
    ap.add_argument("--manifest", default=MANIFEST_PATH)
    args = ap.parse_args()
    if args.command == "sheets":
        cmd_sheets(args.manifest)
    elif args.command == "export":
        cmd_export(args.manifest, args.all)
    else:
        cmd_import(args.manifest, args.write)


if __name__ == "__main__":
    main()
