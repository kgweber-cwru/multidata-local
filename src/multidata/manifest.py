"""The manifest — a SQLite database of Recordings and Videos, the spine of the whole
thing (doc §10).

The two tables matching the two models in `multidata.models`:

  recordings   one row per recorded encounter (source-system metadata)
  videos  one row per physical camera file, usually two per recording — this is
          what every processing stage actually reads/writes

...plus `cameras`, `file_matches` (ingest bookkeeping, §2), `stage_runs`
(what each stage run cost, for scaling estimates), `assignments` (who has
which annotation pass) and `gold`
(one row per gold reference produced, possibly several per recording for excerpt
gold — implementation plan Phase 2.4), none of which have their own
dataclass; `Recording`/`Video`/`Camera` exist because their columns get
constructed programmatically in more than one place, not because every table
needs one.

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

from multidata.models import STATUSES, Camera, Recording, Video
from multidata.paths import MANIFEST_PATH as DEFAULT_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    recording_id TEXT PRIMARY KEY,
    activity_title TEXT NOT NULL DEFAULT '',
    case_title TEXT NOT NULL DEFAULT '',
    learner_group TEXT NOT NULL DEFAULT '',
    room_number TEXT NOT NULL DEFAULT '',
    learner_name TEXT NOT NULL DEFAULT '',
    sp_name TEXT NOT NULL DEFAULT '',
    preceptor_name TEXT NOT NULL DEFAULT '',
    recording_start_time TEXT NOT NULL DEFAULT '',
    consent_ref TEXT NOT NULL DEFAULT '',
    cloud_release INTEGER NOT NULL DEFAULT 0,
    cloud_release_basis TEXT NOT NULL DEFAULT '',
    audio_camera TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS videos (
    recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
    camera TEXT NOT NULL,
    file_number TEXT NOT NULL DEFAULT '',
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
    sync_status TEXT NOT NULL DEFAULT 'pending',
    views_status TEXT NOT NULL DEFAULT 'pending',
    prosody_status TEXT NOT NULL DEFAULT 'pending',
    sync_offset_s REAL,
    sync_agreement REAL,
    asr_model TEXT NOT NULL DEFAULT '',
    asr_commit TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (recording_id, camera)
);

CREATE TABLE IF NOT EXISTS cameras (
    camera_id TEXT PRIMARY KEY,
    room_number TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS file_matches (
    filename TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    recording_id TEXT,
    camera TEXT NOT NULL,
    room TEXT NOT NULL DEFAULT '',
    video_date TEXT NOT NULL,
    video_time TEXT NOT NULL,
    candidates TEXT NOT NULL DEFAULT '',
    matched_at TEXT NOT NULL
);

-- One row per gold reference (docs/asr_provider_implementation_plan.md
-- Phase 2.4, docs/transcription_standards.md §3/§9). `id`, not `recording_id`, is
-- the primary key: a recording can have several gold rows -- excerpt gold from
-- several spans of the same encounter is an explicitly valid strategy
-- (standards §3) for stretching limited annotator time across more strata,
-- so "one gold row per recording" would be the wrong constraint to bake in here.
CREATE TABLE IF NOT EXISTS gold (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
    annotator TEXT NOT NULL DEFAULT '',
    pass1_frozen_at TEXT NOT NULL DEFAULT '',
    gold_at TEXT NOT NULL DEFAULT '',
    standards_version TEXT NOT NULL DEFAULT '',
    span_start REAL,
    span_end REAL,
    notes TEXT NOT NULL DEFAULT '',
    pass1_source TEXT NOT NULL DEFAULT 'blind',
    seed_engine TEXT NOT NULL DEFAULT '',
    seed_model TEXT NOT NULL DEFAULT '',
    seed_sha256 TEXT NOT NULL DEFAULT ''
);

-- Who has which annotation pass of which recording (annotation/README.md).
-- One kit folder per (recording, annotator) -- kits/<recording>/<annotator>/
-- -- so that pair is the key and `pass` is an attribute: one person never
-- does both passes of a recording. Written by make_kit.py, make_pass2_kit.py,
-- eaf_to_gold.py and annotation/assignments.py; never edited by hand. No
-- foreign key to recordings: an abandoned row may outlive its recording.
CREATE TABLE IF NOT EXISTS assignments (
    recording_id TEXT NOT NULL,
    annotator TEXT NOT NULL,
    pass INTEGER NOT NULL CHECK (pass IN (1, 2)),
    route TEXT NOT NULL CHECK (route IN ('blind', 'seeded')),
    camera TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'assigned',
    assigned_on TEXT NOT NULL DEFAULT '',
    returned_on TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (recording_id, annotator)
);

-- Camera views (multidata.views): one row per view a camera has been seen
-- holding. `view_id` is stable per camera (v1, v2, ...). `label` starts
-- provisional (`label_source` 'provisional'); a person's name for it (e.g.
-- Drew's preset name) replaces it with label_source 'confirmed'.
CREATE TABLE IF NOT EXISTS camera_views (
    camera TEXT NOT NULL,
    view_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    label_source TEXT NOT NULL DEFAULT '',
    first_recording_id TEXT NOT NULL DEFAULT '',
    samples TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (camera, view_id)
);

-- How long each video spent in each view; per-stretch detail is in
-- data/views/<recording_id>/<camera>.json. A view is a preset when it recurs:
-- see `view_summary()`.
CREATE TABLE IF NOT EXISTS video_views (
    recording_id TEXT NOT NULL,
    camera TEXT NOT NULL,
    view_id TEXT NOT NULL,
    seconds REAL NOT NULL,
    stretches INTEGER NOT NULL,
    PRIMARY KEY (recording_id, camera, view_id)
);

-- Per-speaker prosody (multidata.prosody): one row per diarized speaker in
-- a recording's audio. Speaker labels are per recording, not people across recordings.
-- pitch_bucket is a rough vocal-register label for sampling only.
CREATE TABLE IF NOT EXISTS speaker_prosody (
    recording_id TEXT NOT NULL,
    camera TEXT NOT NULL,
    speaker TEXT NOT NULL,
    turns INTEGER,
    speaking_s REAL,
    overlap_s REAL,
    voiced_s REAL,
    median_f0_hz REAL,
    f0_p10_hz REAL,
    f0_p90_hz REAL,
    f0_range_st REAL,
    f0_sd_st REAL,
    mean_intensity_db REAL,
    median_hnr_db REAL,
    pitch_bucket TEXT,
    PRIMARY KEY (recording_id, camera, speaker)
);

-- One row per stage run on one video row: what it cost, so the pilot's
-- numbers can be scaled to the full corpus (`record_stage_run`). Written by
-- run_stage.py for every stage, success or failure. `realtime_x` is media
-- seconds processed per wall-clock second (2.0 = twice as fast as playback).
-- `params` and `details` are JSON: the flags that shaped the run, and
-- whatever the stage measured (e.g. pose's decode/detect/pose split).
CREATE TABLE IF NOT EXISTS stage_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    recording_id TEXT NOT NULL,
    camera TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    wall_s REAL NOT NULL,
    media_s REAL,
    realtime_x REAL,
    host TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    gpu TEXT NOT NULL DEFAULT '',
    code_commit TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL DEFAULT '{}',
    details TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT ''
);

-- LearningSpace checklist scores (multidata.learningspace): one row per
-- recording part, section and submission. Points only; item answers stay in
-- LearningSpace. The faculty-observation part's submitter is the preceptor.
CREATE TABLE IF NOT EXISTS recording_scores (
    recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
    case_part TEXT NOT NULL,
    section TEXT NOT NULL,
    submit_id TEXT NOT NULL,
    submitter_id TEXT NOT NULL DEFAULT '',
    submitter_name TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT '',
    achieved REAL,
    possible REAL,
    items INTEGER,
    PRIMARY KEY (recording_id, case_part, section, submit_id)
);

-- LearningSpace room GUID -> our room number, learned from recordings whose
-- room we already know. A GUID seen with two numbers is a conflict to fix.
CREATE TABLE IF NOT EXISTS ls_rooms (
    room_guid TEXT PRIMARY KEY,
    room_number TEXT NOT NULL
);
"""


def connect(path=DEFAULT_PATH):
    """Open the database, creating the schema on first use."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _rename_to_learningspace_terms(conn)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()  # migrations must be durable regardless of whether the
                   # caller commits its own transaction (read-only callers
                   # like `all_recordings` never do, and would otherwise silently
                   # roll back any migration that inserts rows, not just DDL)
    return conn


# One-time rename to LearningSpace's words (2026-11-11, docs/learningspace.md):
# what we called a "case" is LearningSpace's video *recording*; their "case"
# is the scenario. Values are unchanged: recording_id holds the same
# six-digit numbers case_id did. Each step runs only if the old name is there.
_RENAMED_TABLES = (("cases", "recordings"), ("video_case_matches", "file_matches"),
                   ("case_scores", "recording_scores"))
_RENAMED_COLUMNS = (  # (table under its new name, old column, new column)
    ("videos", "recording_id", "file_number"),   # first: frees the name
    ("recordings", "case_id", "recording_id"),
    ("recordings", "event_name", "activity_title"),
    ("recordings", "case_name", "case_title"),
    ("recordings", "group_name", "learner_group"),
    ("recordings", "ls_recording_guid", "ls_recording_id"),
    ("recordings", "ls_case_guid", "ls_case_id"),
    ("videos", "case_id", "recording_id"),
    ("file_matches", "case_id", "recording_id"),
    ("recording_scores", "case_id", "recording_id"),
    ("gold", "case_id", "recording_id"),
    ("stage_runs", "case_id", "recording_id"),
    ("camera_views", "first_case_id", "first_recording_id"),
    ("video_views", "case_id", "recording_id"),
    ("speaker_prosody", "case_id", "recording_id"),
)


def _rename_to_learningspace_terms(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for old, new in _RENAMED_TABLES:
        if old in tables and new not in tables:
            conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
            tables = (tables - {old}) | {new}
    for table, old, new in _RENAMED_COLUMNS:
        if table not in tables:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if old in cols and new not in cols:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    conn.commit()


def _migrate(conn):
    """Additive column migrations for databases created before they existed.

    `CREATE TABLE IF NOT EXISTS` (above) only helps brand-new databases; an
    existing manifest.sqlite needs an explicit ALTER TABLE. Keep this
    idempotent and additive-only -- never drop or rename a column here (the
    one rename, to LearningSpace's terms, is `_rename_to_learningspace_terms`).
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(recordings)")}
    if "audio_camera" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN audio_camera TEXT NOT NULL DEFAULT ''")
    if "preceptor_name" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN preceptor_name TEXT NOT NULL DEFAULT ''")
    # Interlock defaults to 0 (blocked) for every pre-existing row -- see
    # models.Recording and docs/asr_provider_spec.md §9. Fail closed: a recording is
    # cleared for third-party ASR only by explicit, recorded decision.
    if "cloud_release" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN cloud_release INTEGER NOT NULL DEFAULT 0")
    if "cloud_release_basis" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN cloud_release_basis TEXT NOT NULL DEFAULT ''")

    # Camera sync (multidata.sync). sync_offset_s stays NULL until measured:
    # NULL means "unknown", and 0.0 is a real answer (the reference camera).
    vcols = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    if "sync_status" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'pending'")
    if "sync_offset_s" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN sync_offset_s REAL")
    if "sync_agreement" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN sync_agreement REAL")
    if "views_status" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN views_status TEXT NOT NULL DEFAULT 'pending'")
    # How pass 1 was made (standards §9): blind, or seeded from a machine draft.
    gcols = {row["name"] for row in conn.execute("PRAGMA table_info(gold)")}
    for col, decl in (("pass1_source", "TEXT NOT NULL DEFAULT 'blind'"),
                      ("seed_engine", "TEXT NOT NULL DEFAULT ''"),
                      ("seed_model", "TEXT NOT NULL DEFAULT ''"),
                      ("seed_sha256", "TEXT NOT NULL DEFAULT ''")):
        if col not in gcols:
            conn.execute(f"ALTER TABLE gold ADD COLUMN {col} {decl}")
    if "prosody_status" not in vcols:
        conn.execute("ALTER TABLE videos ADD COLUMN prosody_status TEXT NOT NULL DEFAULT 'pending'")

    # LearningSpace links (multidata.learningspace)
    for col in ("ls_recording_id", "ls_activity_id", "ls_event_id", "ls_case_id",
                "ls_learner_id", "ls_room_id", "recording_start_utc", "ls_synced_at"):
        if col not in cols:
            conn.execute(f"ALTER TABLE recordings ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    for col, decl in (("ls_file_id", "TEXT NOT NULL DEFAULT ''"),
                      ("ls_server", "TEXT NOT NULL DEFAULT ''"),
                      ("size_bytes", "INTEGER"),
                      ("download_url", "TEXT NOT NULL DEFAULT ''")):
        if col not in vcols:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {col} {decl}")

    _seed_cameras_once(conn)


def _seed_cameras_once(conn):
    """One-time import of the hand-verified camera->room mapping, from
    whichever of the git-tracked seed (`db_seed/camera_rooms.csv`) or the
    legacy pre-database CSV (`data/room_camera_map.csv`) exists -- only if
    the `cameras` table is still empty, so this never clobbers edits already
    made directly in the database (doc §2/§4)."""
    if conn.execute("SELECT 1 FROM cameras LIMIT 1").fetchone():
        return

    from multidata.paths import ROOT

    for candidate in (ROOT / "db_seed" / "camera_rooms.csv", ROOT / "data" / "room_camera_map.csv"):
        if candidate.exists():
            _import_camera_rooms_csv(conn, candidate)
            return


def _import_camera_rooms_csv(conn, path):
    import csv

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            camera_id = row["camera_id"].strip().strip("'").strip()
            room = row["room"].strip().strip("'").strip()
            conn.execute(
                "INSERT OR REPLACE INTO cameras (camera_id, room_number) VALUES (?, ?)",
                (camera_id, room),
            )


def _insert(conn, table, obj):
    cols = type(obj).columns()
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        tuple(getattr(obj, c) for c in cols),
    )


def add_recording(recording, path=DEFAULT_PATH):
    """Insert one recording. Refuses duplicate recording_ids."""
    with closing(connect(path)) as conn, conn:
        try:
            _insert(conn, "recordings", recording)
        except sqlite3.IntegrityError:
            raise ValueError(f"recording_id {recording.recording_id!r} already in {path}") from None
    return recording


def get_recording(recording_id, path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT * FROM recordings WHERE recording_id = ?", (recording_id,)).fetchone()
    return Recording.from_row(row) if row else None


def all_recordings(path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM recordings ORDER BY recording_id").fetchall()
    return [Recording.from_row(r) for r in rows]


def names_for_recording(recording_id, path=DEFAULT_PATH):
    """{"learner": ..., "patient": ..., "preceptor": ...} for one recording — the
    redaction map `multidata.redact.redact_names` needs, read from the three
    manifest name columns (docs/transcription_standards.md §8). A missing name
    comes back as "" (redact_names treats a falsy name as "skip this role"),
    not an error -- not every recording has a recorded preceptor.
    """
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT learner_name, sp_name, preceptor_name FROM recordings WHERE recording_id = ?",
            (recording_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"recording_id {recording_id!r} not in {path}")
    return {
        "learner": row["learner_name"],
        "patient": row["sp_name"],
        "preceptor": row["preceptor_name"],
    }


def record_gold(recording_id, annotator="", pass1_frozen_at="", gold_at="",
                 standards_version="", span_start=None, span_end=None, notes="",
                 seed=None, path=DEFAULT_PATH):
    """Insert one gold-reference row (implementation plan Phase 2.4). Returns
    the new row's `id`. Never updates an existing row -- annotation is a
    sequence of events (pass 1 frozen, then adjudicated), and each call here
    records one; re-annotating a recording is a new row, not an edit to the old
    one, so the history of what existed when is never destroyed.

    `span_start`/`span_end` (seconds), left `None` for whole-encounter gold,
    record the excerpt's bounds when only part of the encounter was
    annotated (standards §3) -- required for a scorer to know what portion
    of the audio the reference actually covers.

    `seed` is the seed provenance dict for draft-seeded gold
    (`seeding.provenance`); None means pass 1 was blind.
    """
    seed = seed or {}
    with closing(connect(path)) as conn, conn:
        cursor = conn.execute(
            "INSERT INTO gold (recording_id, annotator, pass1_frozen_at, gold_at, "
            "standards_version, span_start, span_end, notes, pass1_source, "
            "seed_engine, seed_model, seed_sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (recording_id, annotator, pass1_frozen_at, gold_at, standards_version,
             span_start, span_end, notes, seed.get("pass1_source", "blind"),
             seed.get("seed_engine", ""), seed.get("seed_model", ""),
             seed.get("seed_sha256", "")),
        )
        return cursor.lastrowid


def gold_for_recording(recording_id, path=DEFAULT_PATH):
    """Every gold row for one recording, oldest first -- possibly more than one
    (standards §3: excerpt gold from several spans is a valid strategy for
    stretching limited annotator time across more strata)."""
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT * FROM gold WHERE recording_id = ? ORDER BY id", (recording_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def all_gold(path=DEFAULT_PATH):
    """Every gold row across every recording, oldest first -- the query that makes
    "which recordings have usable gold?" a query instead of an `ls` on
    benchmarks/references/ (implementation plan Phase 2.4's "done when")."""
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM gold ORDER BY recording_id, id").fetchall()
    return [dict(r) for r in rows]


# --- annotation assignments ------------------------------------------------
#
# The tracker that used to be annotation/assignments.csv, edited by hand at
# every step and wrong more than once. Now the scripts that move a pass along
# write it; `annotation/assignments.py` covers the rest (return, note, check).

ASSIGNMENT_STATUSES = ("assigned", "in_progress", "returned", "gold", "abandoned")
ASSIGNMENT_COLUMNS = ("recording_id", "annotator", "pass", "route", "camera", "status",
                      "assigned_on", "returned_on", "notes")


def _check_assignment_fields(fields):
    unknown = set(fields) - set(ASSIGNMENT_COLUMNS)
    if unknown:
        raise ValueError(f"Unknown columns {sorted(unknown)}")
    if "status" in fields and fields["status"] not in ASSIGNMENT_STATUSES:
        raise ValueError(f"Invalid status {fields['status']!r}; allowed: {ASSIGNMENT_STATUSES}")


def add_assignment(recording_id, annotator, pass_no, route, path=DEFAULT_PATH, **fields):
    """Insert one assignment row. Refuses a second row for the same
    (recording, annotator)."""
    row = {"recording_id": str(recording_id), "annotator": annotator, "pass": int(pass_no),
           "route": route, **fields}
    _check_assignment_fields(row)
    with closing(connect(path)) as conn, conn:
        try:
            conn.execute(f"INSERT INTO assignments ({', '.join(row)}) "
                         f"VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                raise ValueError(f"{annotator} already has an assignment for {recording_id}") from None
            raise
    return get_assignment(recording_id, annotator, path)


def get_assignment(recording_id, annotator, path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT * FROM assignments WHERE recording_id = ? AND annotator = ?",
                           (str(recording_id), annotator)).fetchone()
    return dict(row) if row else None


def update_assignment(recording_id, annotator, path=DEFAULT_PATH, **fields):
    """Set columns on one assignment row. Returns the row as it now is."""
    _check_assignment_fields(fields)
    if fields:
        with closing(connect(path)) as conn, conn:
            cur = conn.execute(
                f"UPDATE assignments SET {', '.join(f'{k} = ?' for k in fields)} "
                "WHERE recording_id = ? AND annotator = ?",
                (*fields.values(), str(recording_id), annotator))
            if cur.rowcount != 1:
                raise KeyError(f"no assignment for {annotator} on {recording_id}")
    return get_assignment(recording_id, annotator, path)


def all_assignments(path=DEFAULT_PATH, recording_id=None):
    """Every assignment row (or one recording's), in recording then pass order."""
    sql, args = "SELECT * FROM assignments", ()
    if recording_id is not None:
        sql, args = sql + " WHERE recording_id = ?", (str(recording_id),)
    with closing(connect(path)) as conn:
        rows = conn.execute(sql + " ORDER BY recording_id, pass, assigned_on, annotator",
                            args).fetchall()
    return [dict(r) for r in rows]


def export_assignments(out_csv, path=DEFAULT_PATH):
    """Dump the table to CSV -- the git-tracked backup
    (`db_seed/assignments.csv`), since the database itself is gitignored."""
    import csv

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ASSIGNMENT_COLUMNS)
        writer.writeheader()
        writer.writerows(all_assignments(path))
    return out_csv


def import_assignments(in_csv, path=DEFAULT_PATH):
    """Load an `export_assignments` CSV into an empty table (restoring the
    backup, or the one-time move from annotation/assignments.csv). Refuses
    if the table already has rows. Returns the number of rows loaded."""
    import csv

    with open(in_csv, newline="") as f:
        rows = [{k: row[k] for k in ASSIGNMENT_COLUMNS} for row in csv.DictReader(f)]
    for row in rows:
        _check_assignment_fields(row)
    with closing(connect(path)) as conn, conn:
        if conn.execute("SELECT 1 FROM assignments LIMIT 1").fetchone():
            raise ValueError(f"assignments in {path} is not empty -- not importing over it")
        conn.executemany(
            f"INSERT INTO assignments ({', '.join(ASSIGNMENT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(ASSIGNMENT_COLUMNS))})",
            [tuple(r.values()) for r in rows])
    return len(rows)


def _camera_sort_key(camera):
    """Numeric cameras sort numerically ("9" before "10"); anything else
    falls back to plain string sort, after all numeric ids."""
    try:
        return (0, int(camera))
    except ValueError:
        return (1, camera)


def ensure_audio_camera(recording_id, path=DEFAULT_PATH):
    """The camera whose audio is canonical for this recording's audio-derived
    stages (audio/asr/diarize/elan) — decided and persisted to
    `recordings.audio_camera` on first call, sticky thereafter.

    A recording's two camera files carry the *same* audio feed (confirmed by
    direct cross-correlation of several camera pairs across rooms — same
    samples once time-aligned, just started a few hundred ms to ~1s apart),
    not two independently-mic'd recordings. So which camera is "the" audio
    source is an arbitrary but permanent choice, not a quality judgment: the
    lowest camera id among the recording's video rows. `pose` is deliberately
    untouched by this — the two cameras' *video* is genuinely different per
    viewpoint (doc §9).

    Also marks every other camera's `audio_status`/`asr_status`/
    `diarize_status`/`elan_status` as `skipped` if still `pending`, so those
    stages run exactly once per recording regardless of which camera's row a
    caller happens to process first. Never touches a row already `done` or
    `failed` — this only short-circuits work that hasn't happened yet.
    """
    with closing(connect(path)) as conn, conn:
        row = conn.execute(
            "SELECT audio_camera FROM recordings WHERE recording_id = ?", (recording_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"recording_id {recording_id!r} not in {path}")
        if row["audio_camera"]:
            return row["audio_camera"]

        cameras = [r["camera"] for r in conn.execute(
            "SELECT camera FROM videos WHERE recording_id = ?", (recording_id,)
        ).fetchall()]
        if not cameras:
            raise KeyError(f"recording_id {recording_id!r} has no video rows in {path}")
        chosen = min(cameras, key=_camera_sort_key)

        conn.execute(
            "UPDATE recordings SET audio_camera = ? WHERE recording_id = ?", (chosen, recording_id)
        )
        for status_col in ("audio_status", "asr_status", "diarize_status", "elan_status"):
            conn.execute(
                f"UPDATE videos SET {status_col} = 'skipped' "
                f"WHERE recording_id = ? AND camera != ? AND {status_col} = 'pending'",
                (recording_id, chosen),
            )
        return chosen


# --- camera -> room mapping ----------------------------------------------
#
# Hand-verified knowledge with no external source to re-derive it from if
# lost (unlike `recordings`, re-importable from the Excel export) -- discovered by
# pulling a few videos per room and eyeballing them (`Camera` in
# `multidata.models`). `camera_id` is the primary key, so (unlike the old
# `data/room_camera_map.csv`) it is structurally impossible for the same
# camera to end up mapped to two different rooms.

def set_camera_room(camera_id, room_number, path=DEFAULT_PATH):
    """Add or update one camera's room."""
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO cameras (camera_id, room_number) VALUES (?, ?) "
            "ON CONFLICT(camera_id) DO UPDATE SET room_number = excluded.room_number",
            (camera_id, room_number),
        )
    return Camera(camera_id=camera_id, room_number=room_number)


def get_camera_room(camera_id, path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)
        ).fetchone()
    return Camera.from_row(row) if row else None


def all_camera_rooms(path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM cameras ORDER BY camera_id").fetchall()
    return [Camera.from_row(r) for r in rows]


def export_camera_rooms(out_csv, path=DEFAULT_PATH):
    """Dump the `cameras` table back to a `room,camera_id` CSV -- the
    git-tracked backup (`db_seed/camera_rooms.csv`) for this hand-verified
    mapping, since the database itself is gitignored. Call this after making
    edits and commit the result."""
    import csv

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["room", "camera_id"])
        for cam in all_camera_rooms(path):
            writer.writerow([cam.room_number, cam.camera_id])
    return out_csv


def add_video(recording_id, camera, path=DEFAULT_PATH, **fields):
    """Register one camera-file, with every stage pending.

    Refuses duplicate (recording_id, camera) pairs, and refuses videos for a recording
    that hasn't been added yet — import/add the recording first.
    """
    video = Video(recording_id=recording_id, camera=camera, **fields)
    with closing(connect(path)) as conn, conn:
        try:
            _insert(conn, "videos", video)
        except sqlite3.IntegrityError as e:
            if "FOREIGN KEY" in str(e):
                raise KeyError(
                    f"recording_id {recording_id!r} not in {path} — add the recording first"
                ) from None
            raise ValueError(f"({recording_id!r}, {camera!r}) already in {path}") from None
    return video


def get_video(recording_id, camera, path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE recording_id = ? AND camera = ?", (recording_id, camera)
        ).fetchone()
    return Video.from_row(row) if row else None


def all_videos(path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM videos ORDER BY recording_id, camera").fetchall()
    return [Video.from_row(r) for r in rows]


def pending(stage, path=DEFAULT_PATH):
    """Video rows whose `<stage>_status` is pending, recording metadata joined in."""
    status_col = f"{stage}_status"
    if status_col not in Video.STATUS_FIELDS:
        stages = sorted(f.removesuffix("_status") for f in Video.STATUS_FIELDS)
        raise ValueError(f"Unknown stage {stage!r}; expected one of {stages}")

    with closing(connect(path)) as conn:
        rows = conn.execute(
            f"SELECT videos.*, recordings.* FROM videos "
            f"JOIN recordings USING (recording_id) WHERE videos.{status_col} = 'pending'"
        ).fetchall()
    return [dict(r) for r in rows]


def update(recording_id, camera, path=DEFAULT_PATH, **fields):
    """Set fields on one video row."""
    bad_status = {k: v for k, v in fields.items()
                  if k.endswith("_status") and v not in STATUSES}
    if bad_status:
        raise ValueError(f"Invalid status {bad_status}; allowed: {sorted(STATUSES)}")
    unknown = set(fields) - set(Video.columns())
    if unknown:
        raise ValueError(f"Unknown columns {sorted(unknown)}")
    if not fields:
        return get_video(recording_id, camera, path)

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with closing(connect(path)) as conn, conn:
        cur = conn.execute(
            f"UPDATE videos SET {set_clause} WHERE recording_id = ? AND camera = ?",
            (*fields.values(), recording_id, camera),
        )
        if cur.rowcount == 0:
            raise KeyError(f"({recording_id!r}, {camera!r}) not in {path}")
    return get_video(recording_id, camera, path)


# --- filename -> recording_id match cache ------------------------------------
#
# Resolving a filename to a recording_id means loading every recording row and scanning
# for a (room, minute) match (`multidata.filematch`) — worth doing once and
# remembering. `status` is one of: "matched", "ambiguous", "no_match",
# "no_room" (camera not yet in the `cameras` table). Only "matched" rows have
# a recording_id; the rest exist so unresolved files show up in one place for you
# to fix by hand (`set_camera_room`, or check the source spreadsheet) and
# re-run — re-matching overwrites the cached row (INSERT OR REPLACE).

def get_cached_match(filename, path=DEFAULT_PATH):
    """The cached match row for `filename`, or None if never attempted."""
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT * FROM file_matches WHERE filename = ?", (filename,)
        ).fetchone()
    return dict(row) if row else None


def record_stage_run(stage, recording_id, camera, status, started_at, wall_s, media_s=None,
                     device="", gpu="", host="", code_commit="", params=None,
                     details=None, error="", path=DEFAULT_PATH):
    """Append one `stage_runs` row (see the table's comment in SCHEMA)."""
    import json

    realtime_x = media_s / wall_s if status == "done" and media_s and wall_s > 0 else None
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO stage_runs (stage, recording_id, camera, status, started_at, wall_s, "
            "media_s, realtime_x, host, device, gpu, code_commit, params, details, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (stage, recording_id, camera, status, started_at, wall_s, media_s, realtime_x,
             host, device or "", gpu or "", code_commit,
             json.dumps(params or {}, default=str), json.dumps(details or {}, default=str),
             error),
        )


def add_camera_view(camera, view_id, first_recording_id, samples, path=DEFAULT_PATH):
    """Register a newly seen view (unlabelled until someone names it)."""
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO camera_views (camera, view_id, first_recording_id, samples, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (str(camera), view_id, first_recording_id, str(samples),
             datetime.now().isoformat(timespec="seconds")))


# The operators' preset names (from Drew, 2026-11-01). A view's label is one
# of these when it matches a preset; "User" is an operator-defined preset.
PRESETS = ("Baseline", "Chair (End of Exam Bed)", "Bed (Full Length of Exam Bed)",
           "Sink", "PC", "User")


def set_view_label(camera, view_id, label, source="confirmed", notes=None,
                   path=DEFAULT_PATH):
    """Name a view, e.g. with the operator's preset name (`PRESETS`).
    `source` is 'provisional' for a guess, 'confirmed' once someone who
    knows has checked it. `notes` (optional) replaces the view's notes."""
    sql = "UPDATE camera_views SET label = ?, label_source = ?"
    args = [label, source]
    if notes is not None:
        sql += ", notes = ?"
        args.append(notes)
    with closing(connect(path)) as conn, conn:
        n = conn.execute(sql + " WHERE camera = ? AND view_id = ?",
                         (*args, str(camera), view_id)).rowcount
    if not n:
        raise KeyError(f"no view {view_id} on camera {camera}")


def set_video_views(recording_id, camera, usage, path=DEFAULT_PATH):
    """Replace one video's per-view time: usage = {view_id: (seconds, stretches)}."""
    with closing(connect(path)) as conn, conn:
        conn.execute("DELETE FROM video_views WHERE recording_id = ? AND camera = ?",
                     (recording_id, str(camera)))
        conn.executemany(
            "INSERT INTO video_views (recording_id, camera, view_id, seconds, stretches) "
            "VALUES (?, ?, ?, ?, ?)",
            [(recording_id, str(camera), v, round(s, 1), n) for v, (s, n) in usage.items()])


def view_summary(path=DEFAULT_PATH, min_recordings=2, min_stretches=3):
    """Every known view with its label, total time, recordings seen in, and
    whether it counts as a preset (seen in `min_recordings`+ recordings, or held
    `min_stretches`+ separate times)."""
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT cv.camera, cv.view_id, cv.label, cv.label_source, cv.samples, "
            "COALESCE(SUM(vv.seconds), 0) AS seconds, COUNT(DISTINCT vv.recording_id) AS recordings, "
            "COALESCE(SUM(vv.stretches), 0) AS stretches "
            "FROM camera_views cv LEFT JOIN video_views vv USING (camera, view_id) "
            "GROUP BY cv.camera, cv.view_id ORDER BY cv.camera, seconds DESC").fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        r["is_preset"] = r["recordings"] >= min_recordings or r["stretches"] >= min_stretches
    return out


def set_speaker_prosody(recording_id, camera, rows, path=DEFAULT_PATH):
    """Replace one recording's per-speaker prosody rows (`prosody.speaker_summary`)."""
    from multidata.prosody import SPEAKER_COLUMNS

    cols = ("recording_id", "camera") + SPEAKER_COLUMNS
    with closing(connect(path)) as conn, conn:
        conn.execute("DELETE FROM speaker_prosody WHERE recording_id = ? AND camera = ?",
                     (recording_id, str(camera)))
        conn.executemany(
            f"INSERT INTO speaker_prosody ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            [(recording_id, str(camera)) + tuple(r[c] for c in SPEAKER_COLUMNS) for r in rows])


def export_speaker_prosody(csv_path, path=DEFAULT_PATH):
    """Every recording's per-speaker prosody as one CSV (what
    data/speaker_pitch_stats.csv used to be, from a notebook)."""
    import csv

    with closing(connect(path)) as conn:
        cur = conn.execute("SELECT * FROM speaker_prosody ORDER BY recording_id, speaker")
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(names)
        w.writerows(rows)
    return len(rows)


def save_match(filename, status, camera, video_date, video_time, path=DEFAULT_PATH,
                recording_id=None, room="", candidates=()):
    """Cache a match attempt (success or failure) for `filename`."""
    if status not in {"matched", "ambiguous", "no_match", "no_room"}:
        raise ValueError(f"Invalid match status {status!r}")
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO file_matches "
            "(filename, status, recording_id, camera, room, video_date, video_time, "
            " candidates, matched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (filename, status, recording_id, camera, room, video_date, video_time,
             ",".join(candidates), datetime.now().isoformat()),
        )


def unresolved_matches(path=DEFAULT_PATH):
    """Cached match rows still needing manual attention (not `matched`)."""
    with closing(connect(path)) as conn:
        rows = conn.execute(
            "SELECT * FROM file_matches WHERE status != 'matched' "
            "ORDER BY filename"
        ).fetchall()
    return [dict(r) for r in rows]


# --- LearningSpace links (multidata.learningspace) ----------------------

def update_recording(recording_id, path=DEFAULT_PATH, **fields):
    """Set columns on one recordings row. Unknown columns are an error."""
    unknown = set(fields) - set(Recording.columns())
    if unknown:
        raise ValueError(f"Unknown columns {sorted(unknown)}")
    if not fields:
        return
    with closing(connect(path)) as conn, conn:
        cur = conn.execute(
            f"UPDATE recordings SET {', '.join(f'{k} = ?' for k in fields)} WHERE recording_id = ?",
            (*fields.values(), recording_id))
        if cur.rowcount != 1:
            raise ValueError(f"no recording {recording_id!r}")


def set_recording_scores(recording_id, rows, path=DEFAULT_PATH):
    """Replace one recording's checklist scores (multidata.learningspace.summarize_scores)."""
    cols = ("case_part", "section", "submit_id", "submitter_id", "submitter_name",
            "submitted_at", "achieved", "possible", "items")
    with closing(connect(path)) as conn, conn:
        conn.execute("DELETE FROM recording_scores WHERE recording_id = ?", (recording_id,))
        conn.executemany(
            f"INSERT INTO recording_scores (recording_id, {', '.join(cols)}) "
            f"VALUES (?, {', '.join('?' * len(cols))})",
            [(recording_id, *(r[c] for c in cols)) for r in rows])


def recording_scores(recording_id, path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM recording_scores WHERE recording_id = ? "
                            "ORDER BY case_part, section, submitted_at", (recording_id,)).fetchall()
    return [dict(r) for r in rows]


def set_ls_room(room_guid, room_number, path=DEFAULT_PATH):
    """Record a room GUID's number. Returns the number already stored when it
    differs (a conflict, left unchanged), else None."""
    with closing(connect(path)) as conn, conn:
        row = conn.execute("SELECT room_number FROM ls_rooms WHERE room_guid = ?",
                           (room_guid,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO ls_rooms VALUES (?, ?)", (room_guid, str(room_number)))
            return None
        return row["room_number"] if row["room_number"] != str(room_number) else None


def all_ls_rooms(path=DEFAULT_PATH):
    with closing(connect(path)) as conn:
        return {r["room_guid"]: r["room_number"]
                for r in conn.execute("SELECT * FROM ls_rooms").fetchall()}


def prune_recordings_without_videos(path=DEFAULT_PATH, dry_run=True):
    """Drop recordings with no videos row and nothing else pointing at them (gold,
    scores, stage runs, matches). Returns the recording_ids dropped (or that would
    be). The Excel export can always add them back."""
    sql = """SELECT recording_id FROM recordings c
             WHERE NOT EXISTS (SELECT 1 FROM videos v WHERE v.recording_id = c.recording_id)
               AND NOT EXISTS (SELECT 1 FROM gold g WHERE g.recording_id = c.recording_id)
               AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.recording_id = c.recording_id)
               AND NOT EXISTS (SELECT 1 FROM recording_scores s WHERE s.recording_id = c.recording_id)
               AND NOT EXISTS (SELECT 1 FROM stage_runs r WHERE r.recording_id = c.recording_id)
               AND NOT EXISTS (SELECT 1 FROM file_matches m WHERE m.recording_id = c.recording_id)
             ORDER BY recording_id"""
    with closing(connect(path)) as conn, conn:
        ids = [r["recording_id"] for r in conn.execute(sql).fetchall()]
        if not dry_run:
            conn.executemany("DELETE FROM recordings WHERE recording_id = ?", [(i,) for i in ids])
    return ids


# --- Excel import ------------------------------------------------------

_SHEET_FIELD_MAP = {
    "Event": "activity_title",
    "Case": "case_title",
    "Group": "learner_group",
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


def _recording_from_sheet(recording_id, worksheet):
    """Parse the 'Video Record Information' label/value block at the top of
    one sheet (doc §10 / source-system export). Row order isn't load-bearing
    — this reads by label, not position."""
    values = {}
    for label, value, *_ in worksheet.iter_rows(min_row=2, max_row=9, values_only=True):
        if label in _SHEET_FIELD_MAP:
            values[_SHEET_FIELD_MAP[label]] = "" if value is None else str(value)
        elif label == "Recording start":
            values["recording_start_time"] = _parse_recording_start(value)
    return Recording(recording_id=recording_id, **values)


def import_video_manifest_excel(xlsx_path, path=DEFAULT_PATH):
    """Unpack a source-system video-manifest workbook and add any recordings
    missing from the database. One sheet per encounter; sheet title = recording_id
    (see `data/cse2b_videos.xlsx` for an example export).

    Existing recordings are left untouched — this only ever adds rows. Returns
    {"added": [recording_id, ...], "skipped": [recording_id, ...]}.
    """
    import openpyxl  # md-utility only; kept out of module-level imports

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    added, skipped = [], []
    with closing(connect(path)) as conn, conn:
        for ws in wb.worksheets:
            recording_id = ws.title
            exists = conn.execute(
                "SELECT 1 FROM recordings WHERE recording_id = ?", (recording_id,)
            ).fetchone()
            if exists:
                skipped.append(recording_id)
                continue
            _insert(conn, "recordings", _recording_from_sheet(recording_id, ws))
            added.append(recording_id)
    return {"added": added, "skipped": skipped}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("excel_file_path")
    args = ap.parse_args()
    result = import_video_manifest_excel(args.excel_file_path)
    print(f'Added {len(result["added"])} new recordings to database, skipped {result["skipped"]} recordings')

if __name__ == '__main__':
    main()

