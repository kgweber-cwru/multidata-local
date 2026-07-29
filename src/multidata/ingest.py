"""Stage 1: ingest — validate and fingerprint raw video (pipeline doc §4)."""
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

FILENAME_RE = re.compile(
    r"^(?P<date>\d{8})-(?P<time>\d{6})-(?P<recording_id>v\d+)-(?P<camera>\d+)$"
)


def parse_filename(path) -> dict:
    """Parse the source-system naming spec (doc §2):

        <date:YYYYMMDD>-<time:HHMMSS>-<recording_id>-<camera>.<ext>
        e.g. 20260224-132704-v308933-41.mp4

    The second field is a timestamp (13:27:04 here), NOT a case_id — there is
    no case_id anywhere in the filename. `data/raw/` is a flat directory of
    files named to this convention; nothing here cross-checks a containing
    folder. To find which case a file belongs to, match `date`+`time` (the
    cases table only carries minute precision — see `casematch` module
    docstring) and `camera`'s room (via the manifest's `cameras` table)
    against the cases table: `multidata.casematch.resolve()`.
    """
    path = Path(path)
    m = FILENAME_RE.match(path.stem)
    if not m:
        raise ValueError(
            f"{path.name} doesn't match <date>-<time>-<recording_id>-<camera> "
            f"(doc §2), e.g. 20260224-132704-v308933-41.mp4"
        )
    parsed = m.groupdict()
    time_str = parsed["time"]
    return {
        "date": datetime.strptime(parsed["date"], "%Y%m%d").date().isoformat(),
        "time": f"{time_str[0:2]}:{time_str[2:4]}:{time_str[4:6]}",
        "recording_id": parsed["recording_id"],
        "camera": parsed["camera"],
    }


def probe(path) -> dict:
    """ffprobe -> dict of format + streams. Requires ffmpeg on PATH."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def sha256(path, buf: int = 1 << 20) -> str:
    """Streaming SHA-256 — detects silent corruption / duplicate ingests."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize(path) -> dict:
    """Ingest-time facts: identity (camera/recording_id/video_date/video_time,
    parsed from the filename) plus duration, fps, size, audio presence (ffprobe
    + sha256).

    No `case_id` here — the filename doesn't carry one (see `parse_filename`).
    Resolve it separately with `multidata.casematch.resolve()` and pass it to
    `manifest.add_video(case_id=..., **summarize(path))`.

    `path` can be given any way that actually resolves from the caller's own
    working directory (absolute, `data/raw/x.mp4` from the repo root,
    `../data/raw/x.mp4` from a notebook in `notebooks/`, ...) — the stored
    `filepath` is always normalized relative to `multidata.paths.ROOT`, so a
    notebook's cwd can never leak a fragile relative path into the manifest.
    """
    from multidata.paths import ROOT

    parsed = parse_filename(path)
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])

    fps = None
    if v and "/" in v.get("avg_frame_rate", "0/0"):
        num, den = v["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else None

    abs_path = Path(path).resolve()
    try:
        stored_path = str(abs_path.relative_to(ROOT))
    except ValueError:
        stored_path = str(abs_path)  # outside the repo tree; store absolute rather than guess

    return {
        "camera": parsed["camera"],
        "recording_id": parsed["recording_id"],
        "video_date": parsed["date"],
        "video_time": parsed["time"],
        "filepath": stored_path,
        "sha256": sha256(path),
        "duration_s": float(info["format"].get("duration", 0.0)),
        "fps": fps,
        "width": v.get("width") if v else None,
        "height": v.get("height") if v else None,
        "has_audio": has_audio,
    }
