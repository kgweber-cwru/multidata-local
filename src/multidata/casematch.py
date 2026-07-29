"""Resolve a raw video file to its case_id (pipeline doc §2 / §4).

The source filename (`<date>-<time>-<recording_id>-<camera>.mp4`) carries no
case_id at all — the only link back to the source system's per-case metadata
is:

  1. **timestamp**: the case's `recording_start_time` (from the Excel import,
     see `manifest._parse_recording_start`) always has its seconds truncated
     to `:00`, while the filename's time field is full HH:MM:SS. So videos can
     only be matched to a case at **minute** granularity, not exact-second.
  2. **room, via camera**: minute-granularity timestamps are not unique on
     their own — two cases in different rooms can start in the same minute.
     `camera` disambiguates via the room it's physically installed in, looked
     up from the hand-verified `data/room_camera_map.csv`.

Camera→room is deliberately a small hand-maintained CSV, not something
inferred from the data. An earlier draft (`notebooks/spreadsheet_ingester.ipynb`)
tried inferring it by intersecting the candidate rooms of every video seen for
a camera; that is unreliable for the same reason minute-only timestamps are
ambiguous in the first place; do not resurrect that approach. Extend
`data/room_camera_map.csv` by hand as you confirm more camera/room pairs.

Matches (successful or not) are cached in the manifest's
`video_case_matches` table (`manifest.get_cached_match` / `save_match`) so
this scan — which is O(all cases) per file — doesn't have to be redone on
every run, and so ambiguous/unmatched files are recorded in one place for
manual follow-up (`manifest.unresolved_matches()`).
"""
from datetime import datetime
from pathlib import Path

from multidata import ingest, manifest

DEFAULT_CAMERA_MAP_PATH = Path("/Users/kxw680/projects/multidata-local/data/room_camera_map.csv")


class CaseMatchError(Exception):
    """Raised when a video can't be resolved to exactly one case.

    `status` is one of "no_room", "no_match", "ambiguous" — mirrors the
    `video_case_matches.status` values that aren't "matched". `candidates`
    holds the competing case_ids when status is "ambiguous".
    """

    def __init__(self, message, status, candidates=()):
        super().__init__(message)
        self.status = status
        self.candidates = tuple(candidates)


def _normalize(value) -> str:
    """'09' -> '9', "'09'" -> '9' (room_camera_map.csv's cells carry literal
    single-quotes, an artifact of the leading-zero-as-text export), '00 LA'
    -> '00 LA'. Lets int Room cells (Excel) compare equal to string room
    cells (CSV)."""
    s = str(value).strip().strip("'").strip()
    return str(int(s)) if s.lstrip("-").isdigit() else s.upper()


def load_camera_room_map(path=DEFAULT_CAMERA_MAP_PATH) -> dict:
    """camera_id -> room_number, both normalized, from the hand-verified
    `data/room_camera_map.csv` (columns: room, camera_id).

    Raises if the same camera_id maps to two different rooms — that means
    the CSV has a mistake, not that the camera moved.
    """
    import csv

    mapping = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            camera = _normalize(row["camera_id"])
            room = _normalize(row["room"])
            if camera in mapping and mapping[camera] != room:
                raise ValueError(
                    f"camera {camera!r} maps to conflicting rooms "
                    f"{mapping[camera]!r} and {room!r} in {path}"
                )
            mapping[camera] = room
    return mapping


def _video_minute(video_date: str, video_time: str) -> datetime:
    return datetime.fromisoformat(f"{video_date}T{video_time}").replace(
        second=0, microsecond=0
    )


def _case_minute(recording_start_time: str):
    if not recording_start_time:
        return None
    try:
        return datetime.fromisoformat(recording_start_time).replace(
            second=0, microsecond=0
        )
    except ValueError:
        return None


def match_case(video_date, video_time, camera, cases, camera_room_map) -> str:
    """Return the one case_id whose room (via `camera_room_map`) and
    `recording_start_time` (truncated to the minute) match. Raises
    `CaseMatchError` if the camera has no known room, or there isn't exactly
    one match.
    """
    room = camera_room_map.get(_normalize(camera))
    if room is None:
        raise CaseMatchError(
            f"camera {camera!r} isn't in room_camera_map.csv yet", status="no_room"
        )

    target = _video_minute(video_date, video_time)
    candidates = [
        c.case_id
        for c in cases
        if _normalize(c.room_number) == room and _case_minute(c.recording_start_time) == target
    ]

    if not candidates:
        raise CaseMatchError(
            f"no case in room {room!r} recorded at {target.isoformat()}",
            status="no_match",
        )
    if len(candidates) > 1:
        raise CaseMatchError(
            f"{len(candidates)} cases in room {room!r} recorded at "
            f"{target.isoformat()}: {candidates}",
            status="ambiguous",
            candidates=candidates,
        )
    return candidates[0]


def resolve(path, manifest_path=manifest.DEFAULT_PATH,
            camera_map_path=DEFAULT_CAMERA_MAP_PATH) -> str:
    """Resolve one raw video file to its case_id, using the persistent cache
    when available.

    Raises `CaseMatchError` (and caches the failure) if unresolved — call
    `manifest.unresolved_matches()` afterwards to see everything that needs a
    manual look (missing camera/room mapping, no candidate case, or more than
    one).
    """
    filename = Path(path).name
    cached = manifest.get_cached_match(filename, manifest_path)
    if cached is not None and cached["status"] == "matched":
        return cached["case_id"]

    parsed = ingest.parse_filename(path)
    camera_room_map = load_camera_room_map(camera_map_path)
    cases = manifest.all_cases(manifest_path)

    try:
        case_id = match_case(
            parsed["date"], parsed["time"], parsed["camera"], cases, camera_room_map
        )
    except CaseMatchError as e:
        manifest.save_match(
            filename, status=e.status, camera=parsed["camera"],
            video_date=parsed["date"], video_time=parsed["time"],
            path=manifest_path, candidates=e.candidates,
        )
        raise

    room = camera_room_map[_normalize(parsed["camera"])]
    manifest.save_match(
        filename, status="matched", camera=parsed["camera"], room=room,
        video_date=parsed["date"], video_time=parsed["time"],
        path=manifest_path, case_id=case_id,
    )
    return case_id
