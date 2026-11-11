"""Domain models for the manifest database (pipeline doc §10).

Denormalized into two entities because that is how the data actually arrives:
the source system's Excel export gives **recording-level** facts (event, learner,
room, recording time...) once per recorded encounter, while pipeline
processing facts (hashes, dimensions, per-stage status...) exist once per
**physical camera file** — usually two videos per recording (see the raw-ingest
naming convention in pipeline doc §2).

Stdlib-only (`dataclasses`), so this module imports in both the md-speech and
md-pose envs, same as `manifest`.
"""
from dataclasses import dataclass, fields
from typing import Optional

STATUSES = {"pending", "done", "failed", "skipped"}


@dataclass
class Recording:
    """One recorded encounter, as exported by the source system.

    `recording_id` is the source system's own identifier (matches the
    `data/raw/<recording_id>/` folder name, and the sheet title in the source
    system's Excel video-manifest export).

    `audio_camera` is the camera whose audio is canonical for this recording's
    audio-derived stages (audio/asr/diarize/elan) -- see
    `manifest.ensure_audio_camera()`. Empty until a video row exists and one
    of those stages first runs for the recording; sticky once set.

    `learner_name`/`sp_name`/`preceptor_name` double as the **redaction map**
    for gold references: annotators transcribe names verbatim and redaction is
    applied mechanically at export/scoring time from these columns
    (docs/transcription_standards.md §8). That is also why manifest.sqlite is
    gitignored.

    `cloud_release` is the **safety interlock** for third-party ASR providers
    (docs/asr_provider_spec.md §9): 0 by default, and no adapter may send a
    recording's audio off this machine without it. Today's corpus is uniformly
    simulated encounters from our sim center, so it sorts nothing -- its job is to already
    exist on the day real clinical data lands. `cloud_release_basis` records
    *why* a recording is eligible (e.g. "simulated_patient",
    "consented_third_party"), because a bare boolean can't be audited.
    """

    recording_id: str
    activity_title: str = ""
    case_title: str = ""
    learner_group: str = ""
    room_number: str = ""
    learner_name: str = ""
    sp_name: str = ""
    preceptor_name: str = ""
    recording_start_time: str = ""
    consent_ref: str = ""
    cloud_release: int = 0
    cloud_release_basis: str = ""
    audio_camera: str = ""
    # LearningSpace links (multidata.learningspace; docs/learningspace.md).
    # ls_recording_id is LearningSpace's identity for this recording.
    ls_recording_id: str = ""
    ls_activity_id: str = ""
    ls_event_id: str = ""
    ls_case_id: str = ""
    ls_learner_id: str = ""
    ls_room_id: str = ""
    recording_start_utc: str = ""
    ls_synced_at: str = ""

    @classmethod
    def columns(cls):
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_row(cls, row):
        return cls(**{c: row[c] for c in cls.columns()})

@dataclass
class Camera:
    """One physical camera, hand-mapped to the room it lives in.

    The camera_id is the last dash-delimited value in the raw filename (doc
    section 2). No metadata field anywhere states which room a camera is in --
    this is discovered by pulling a few videos per room and eyeballing them,
    then recorded via `manifest.set_camera_room()` (the `cameras` table --
    `camera_id` is its primary key, so a camera can never end up stored under
    two different rooms). `manifest.export_camera_rooms()` writes the durable,
    git-tracked backup at `db_seed/camera_rooms.csv` (the database itself is
    gitignored, and this hand-verified knowledge has no other source to
    reconstruct it from). Do not try to infer this mapping automatically from
    timestamps -- recordings can share a recording minute across rooms, which makes
    inference unreliable (see the `filematch` module docstring).
    """

    camera_id: str
    room_number: str

    @classmethod
    def columns(cls):
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_row(cls, row):
        return cls(**{c: row[c] for c in cls.columns()})

@dataclass
class Video:
    """One camera file belonging to a Recording. Every `*_status` starts `pending`.

    `camera` and `file_number` come from the source filename spec (doc
    section 2): `<date>-<time>-<file_number>-<camera>.mp4`, e.g.
    `20260224-132704-v308933-41.mp4` -> date "20260224", time "132704"
    (13:27:04), file_number "v308933" (the source system's internal
    recording/asset id, aka "video number"), camera "41".

    Important: the filename's second field is a timestamp, not the recording_id --
    there is no recording_id anywhere in the filename. `recording_id` below is resolved
    after the fact by matching (video date+time, truncated to the minute, and
    camera-to-room) against the recordings table -- see
    `multidata.filematch.resolve()`. Never parse it out of the filename.
    """

    recording_id: str
    camera: str
    file_number: str = ""
    video_date: str = ""
    video_time: str = ""
    filepath: str = ""
    sha256: str = ""
    duration_s: Optional[float] = None
    fps: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    has_audio: Optional[bool] = None
    audio_path: str = ""
    ingest_ok: Optional[bool] = None
    audio_status: str = "pending"
    asr_status: str = "pending"
    diarize_status: str = "pending"
    elan_status: str = "pending"
    pose_status: str = "pending"
    sync_status: str = "pending"
    views_status: str = "pending"
    prosody_status: str = "pending"
    sync_offset_s: Optional[float] = None
    sync_agreement: Optional[float] = None
    asr_model: str = ""
    asr_commit: str = ""
    notes: str = ""
    # LearningSpace's record of this file (multidata.learningspace)
    ls_file_id: str = ""
    ls_server: str = ""
    size_bytes: Optional[int] = None
    download_url: str = ""

    STATUS_FIELDS = (
        "audio_status", "asr_status", "diarize_status", "elan_status", "pose_status",
        "sync_status", "views_status", "prosody_status",
    )

    @classmethod
    def columns(cls):
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_row(cls, row):
        return cls(**{c: row[c] for c in cls.columns()})


def upgrade_keys(d):
    """A kit.json or .gold.json written before the rename to LearningSpace's
    terms (2026-11-11) says `case_id`; it means `recording_id`. Returns `d`
    with the new key (same value). Annotators' kits stay as they are."""
    if "case_id" in d and "recording_id" not in d:
        d = {("recording_id" if k == "case_id" else k): v for k, v in d.items()}
    return d
