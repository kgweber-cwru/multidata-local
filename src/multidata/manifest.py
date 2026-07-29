"""The manifest — a SQLite database of Cases and Videos, the spine of the whole
thing (doc §10).

Two tables, matching the two models in `multidata.models`:

  cases   one row per recorded encounter (source-system metadata)
  videos  one row per physical camera file, usually two per case — this is
          what every processing stage actually reads/writes

Every stage reads the manifest, processes only `pending` video rows, writes
outputs and updates status. That is what makes batch runs idempotent and
resumable: kill it overnight, restart, it picks up where it left off.

Stdlib `sqlite3` on purpose — this module has to import in **both** the
md-speech and md-pose envs, and md-pose has no pandas/openpyxl. The Excel
importer needs `openpyxl` (md-utility only), so it's imported lazily inside
`import_video_manifest_excel` rather than at module scope.
"""
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
import argparse

from multidata.models import STATUSES, Case, Video

DEFAULT_PATH = Path("manifest.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL DEFAULT '',
    case_name TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    room_number TEXT NOT NULL DEFAULT '',
    learner_name TEXT NOT NULL DEFAULT '',
    sp_name TEXT NOT NULL DEFAULT '',
    recording_start_time TEXT NOT NULL DEFAULT '',
    consent_ref TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS videos (
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    camera TEXT NOT NULL,
    recording_id TEXT NOT NULL DEFAULT '',
    video_date TEXT NOT NULL DEFAULT '',
    video_time TEXT NOT NULL DEFAULT '',
    filepath TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    duration_s REAL,
    fps REAL,
    width INTEGER,
    height INTEGER,
    has_audio INTEGER,
    audio_path TEXT NOT NULL DEFAULT '',
    ingest_ok INTEGER,
    audio_status TEXT NOT NULL DEFAULT 'pending',
    asr_status TEXT NOT NULL DEFAULT 'pending',
    diarize_status TEXT NOT NULL DEFAULT 'pending',
    elan_status TEXT NOT NULL DEFAULT 'pending',
    pose_status TEXT NOT NULL DEFAULT 'pending',
    asr_model TEXT NOT NULL DEFAULT '',
    asr_commit TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (case_id, camera)
);

CREATE TABLE IF NOT EXISTS video_case_matches (
    filename TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    case_id TEXT,
    camera TEXT NOT NULL,
    room TEXT NOT NULL DEFAULT '',
    video_date TEXT NOT NULL,
    video_time TEXT NOT NULL,
    candidates TEXT NOT NULL DEFAULT '',
    matched_at TEXT NOT NULL
);
"""


def connect(path=DEFAULT_PATH):
    """Open the database, creating the schema on first use."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _insert(conn, table, obj):
    cols = type(obj).columns()
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        tuple(getattr(obj, c) for c in cols),
    )


def add_case(case, path=DEFAULT_PATH):
    """Insert one case. Refuses duplicate case_ids."""
    with closing(connect(path)) as conn, conn:
        try:
            _insert(conn, "cases", case)
        except sqlite3.IntegrityError:
            raise ValueError(f"case_id {case.case_id!r} already in {path}") from None
    return case


def get_case(case_id, path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    return Case.from_row(row) if row else None


def all_cases(path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM cases ORDER BY case_id").fetchall()
    return [Case.from_row(r) for r in rows]


def add_video(case_id, camera, path=DEFAULT_PATH, **fields):
    """Register one camera-file, with every stage pending.

    Refuses duplicate (case_id, camera) pairs, and refuses videos for a case
    that hasn't been added yet — import/add the case first.
    """
    video = Video(case_id=case_id, camera=camera, **fields)
    with closing(connect(path)) as conn, conn:
        try:
            _insert(conn, "videos", video)
        except sqlite3.IntegrityError as e:
            if "FOREIGN KEY" in str(e):
                raise KeyError(
                    f"case_id {case_id!r} not in {path} — add the case first"
                ) from None
            raise ValueError(f"({case_id!r}, {camera!r}) already in {path}") from None
    return video


def get_video(case_id, camera, path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE case_id = ? AND camera = ?", (case_id, camera)
        ).fetchone()
    return Video.from_row(row) if row else None


def all_videos(path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM videos ORDER BY case_id, camera").fetchall()
    return [Video.from_row(r) for r in rows]


def pending(stage, path=DEFAULT_PATH):
    """Video rows whose `<stage>_status` is pending, case metadata joined in."""
    status_col = f"{stage}_status"
    if status_col not in Video.STATUS_FIELDS:
        stages = sorted(f.removesuffix("_status") for f in Video.STATUS_FIELDS)
        raise ValueError(f"Unknown stage {stage!r}; expected one of {stages}")

    with closing(connect(path)) as conn:
        rows = conn.execute(
            f"SELECT videos.*, cases.* FROM videos "
            f"JOIN cases USING (case_id) WHERE videos.{status_col} = 'pending'"
        ).fetchall()
    return [dict(r) for r in rows]


def update(case_id, camera, path=DEFAULT_PATH, **fields):
    """Set fields on one video row."""
    bad_status = {k: v for k, v in fields.items()
                  if k.endswith("_status") and v not in STATUSES}
    if bad_status:
        raise ValueError(f"Invalid status {bad_status}; allowed: {sorted(STATUSES)}")
    unknown = set(fields) - set(Video.columns())
    if unknown:
        raise ValueError(f"Unknown columns {sorted(unknown)}")
    if not fields:
        return get_video(case_id, camera, path)

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with closing(connect(path)) as conn, conn:
        cur = conn.execute(
            f"UPDATE videos SET {set_clause} WHERE case_id = ? AND camera = ?",
            (*fields.values(), case_id, camera),
        )
        if cur.rowcount == 0:
            raise KeyError(f"({case_id!r}, {camera!r}) not in {path}")
    return get_video(case_id, camera, path)


# --- filename -> case_id match cache ------------------------------------
#
# Resolving a filename to a case_id means loading every case row and scanning
# for a (room, minute) match (`multidata.casematch`) — worth doing once and
# remembering. `status` is one of: "matched", "ambiguous", "no_match",
# "no_room" (camera not yet in room_camera_map.csv). Only "matched" rows have
# a case_id; the rest exist so unresolved files show up in one place for you
# to fix by hand (correct room_camera_map.csv, or the source spreadsheet) and
# re-run — re-matching overwrites the cached row (INSERT OR REPLACE).

def get_cached_match(filename, path=DEFAULT_PATH):
    """The cached match row for `filename`, or None if never attempted."""
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT * FROM video_case_matches WHERE filename = ?", (filename,)
        ).fetchone()
    return dict(row) if row else None


def save_match(filename, status, camera, video_date, video_time, path=DEFAULT_PATH,
                case_id=None, room="", candidates=()):
    """Cache a match attempt (success or failure) for `filename`."""
    if status not in {"matched", "ambiguous", "no_match", "no_room"}:
        raise ValueError(f"Invalid match status {status!r}")
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO video_case_matches "
            "(filename, status, case_id, camera, room, video_date, video_time, "
            " candidates, matched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (filename, status, case_id, camera, room, video_date, video_time,
             ",".join(candidates), datetime.now().isoformat()),
        )


def unresolved_matches(path=DEFAULT_PATH):
    """Cached match rows still needing manual attention (not `matched`)."""
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT * FROM video_case_matches WHERE status != 'matched' "
            "ORDER BY filename"
        ).fetchall()
    return [dict(r) for r in rows]


# --- Excel import ------------------------------------------------------

_SHEET_FIELD_MAP = {
    "Event": "event_name",
    "Case": "case_name",
    "Group": "group_name",
    "Room": "room_number",
    "Learners": "learner_name",
    "SPs": "sp_name",
}


def _parse_recording_start(value):
    """'04/21/2025 10:22 am' -> ISO 8601. Falls back to the raw string."""
    if not value:
        return ""
    try:
        return datetime.strptime(str(value), "%m/%d/%Y %I:%M %p").isoformat()
    except ValueError:
        return str(value)


def _case_from_sheet(case_id, worksheet):
    """Parse the 'Video Record Information' label/value block at the top of
    one sheet (doc §10 / source-system export). Row order isn't load-bearing
    — this reads by label, not position."""
    values = {}
    for label, value, *_ in worksheet.iter_rows(min_row=2, max_row=9, values_only=True):
        if label in _SHEET_FIELD_MAP:
            values[_SHEET_FIELD_MAP[label]] = "" if value is None else str(value)
        elif label == "Recording start":
            values["recording_start_time"] = _parse_recording_start(value)
    return Case(case_id=case_id, **values)


def import_video_manifest_excel(xlsx_path, path=DEFAULT_PATH):
    """Unpack a source-system video-manifest workbook and add any cases
    missing from the database. One sheet per encounter; sheet title = case_id
    (see `data/cse2b_videos.xlsx` for an example export).

    Existing cases are left untouched — this only ever adds rows. Returns
    {"added": [case_id, ...], "skipped": [case_id, ...]}.
    """
    import openpyxl  # md-utility only; kept out of module-level imports

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    added, skipped = [], []
    with closing(connect(path)) as conn, conn:
        for ws in wb.worksheets:
            case_id = ws.title
            exists = conn.execute(
                "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if exists:
                skipped.append(case_id)
                continue
            _insert(conn, "cases", _case_from_sheet(case_id, ws))
            added.append(case_id)
    return {"added": added, "skipped": skipped}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_file_path")
    args = ap.parse_args()
    result = import_video_manifest_excel(args.excel_file_path)
    print(f'Added {len(result["added"])} new cases to database, skipped {result["skipped"]} cases')

if __name__ == '__main__':
    main()

