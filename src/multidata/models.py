"""Domain models for the manifest database (pipeline doc §10).

Denormalized into two entities because that is how the data actually arrives:
the source system's Excel export gives **case-level** facts (event, learner,
room, recording time...) once per recorded encounter, while pipeline
processing facts (hashes, dimensions, per-stage status...) exist once per
**physical camera file** — usually two videos per case (see the raw-ingest
naming convention in pipeline doc §2).

Stdlib-only (`dataclasses`), so this module imports in both the md-speech and
md-pose envs, same as `manifest`.
"""
from dataclasses import dataclass, fields
from typing import Optional

STATUSES = {"pending", "done", "failed", "skipped"}


@dataclass
class Case:
    """One recorded encounter, as exported by the source system.

    `case_id` is the source system's own identifier (matches the
    `data/raw/<case_id>/` folder name, and the sheet title in the source
    system's Excel video-manifest export).
    """

    case_id: str
    event_name: str = ""
    case_name: str = ""
    group_name: str = ""
    room_number: str = ""
    learner_name: str = ""
    sp_name: str = ""
    recording_start_time: str = ""
    consent_ref: str = ""

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
    then recorded in `data/room_camera_map.csv`
    (`load_camera_room_map()` in `multidata.casematch`). Extend that CSV as
    more camera/room pairs are confirmed; do not try to infer this mapping
    automatically from timestamps -- cases can share a recording minute across
    rooms, which makes inference unreliable (see the `casematch` module
    docstring).
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
    """One camera file belonging to a Case. Every `*_status` starts `pending`.

    `camera` and `recording_id` come from the source filename spec (doc
    section 2): `<date>-<time>-<recording_id>-<camera>.mp4`, e.g.
    `20260224-132704-v308933-41.mp4` -> date "20260224", time "132704"
    (13:27:04), recording_id "v308933" (the source system's internal
    recording/asset id, aka "video number"), camera "41".

    Important: the filename's second field is a timestamp, not the case_id --
    there is no case_id anywhere in the filename. `case_id` below is resolved
    after the fact by matching (video date+time, truncated to the minute, and
    camera-to-room) against the cases table -- see
    `multidata.casematch.resolve()`. Never parse it out of the filename.
    """

    case_id: str
    camera: str
    recording_id: str = ""
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
    asr_model: str = ""
    asr_commit: str = ""
    notes: str = ""

    STATUS_FIELDS = (
        "audio_status", "asr_status", "diarize_status", "elan_status", "pose_status",
    )

    @classmethod
    def columns(cls):
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_row(cls, row):
        return cls(**{c: row[c] for c in cls.columns()})
