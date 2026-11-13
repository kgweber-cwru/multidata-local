"""What every transcriber shares, local or cloud (docs/asr_provider_spec.md §4,
implementation plan 3.1).

A transcriber starts from a `recording_id`, takes the parameters its vendor's
models accept, and returns a `Transcription`: the vendor's response verbatim
(`native`), the normalized record every consumer reads (`record`), and every
parameter that produced them (`params`, also in `record["provenance"]`).

Subclasses supply three things: a `Params` dataclass (so a misspelled or
foreign parameter fails at construction, naming itself), `call()` (send the
audio, return the response) and `normalize()` (response -> segments). The
manifest lookup, the cloud interlock, stamping and validation live here, once.
"""
import dataclasses
import datetime
import importlib.metadata
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from multidata import manifest, records
from multidata.paths import DATA_DIR, MANIFEST_PATH, ROOT


class CloudReleaseError(PermissionError):
    """A recording without `cloud_release` was about to leave this machine (spec §9)."""


@dataclass
class Transcription:
    recording_id: str
    camera: str
    engine: str
    model: str
    params: dict    # every parameter that shaped the result, after defaults
    native: object  # the vendor's response, verbatim (JSON-able)
    record: dict    # the normalized record (spec §4); params are in its provenance

    def stem(self):
        return f"{self.camera}_{self.engine}_{self.model}"

    def save(self, out_dir=None, overwrite=False):
        """Write `<camera>_<engine>_<model>.json` (normalized) and `.raw.json`
        (native) to `out_dir`, default `data/transcripts/<recording_id>/`
        (spec §7). Refuses to replace an existing file unless `overwrite`:
        the default directory also holds the pipeline's own transcripts, and
        seeded annotation kits were built from some of them."""
        out_dir = Path(out_dir) if out_dir else DATA_DIR / "transcripts" / self.recording_id
        normalized = out_dir / f"{self.stem()}.json"
        raw = out_dir / f"{self.stem()}.raw.json"
        if not overwrite:
            existing = [p for p in (normalized, raw) if p.exists()]
            if existing:
                raise FileExistsError(f"{existing[0]} exists -- pass overwrite=True to replace it")
        out_dir.mkdir(parents=True, exist_ok=True)
        normalized.write_text(json.dumps(self.record, indent=2))
        raw.write_text(json.dumps(self.native, indent=2))
        return normalized, raw


class Transcriber:
    engine = None        # registry name, as in providers.yaml
    kind = "local"       # "local" | "cloud"
    Params = None        # a dataclass of everything this engine accepts
    packages = ()        # distributions whose versions go into provenance

    def __init__(self, **params):
        self.params = self.Params(**params)   # an unknown name is a TypeError here
        self.check_params()

    def check_params(self):
        """Raise ValueError for combinations the vendor rejects."""

    def resolved_params(self):
        return dataclasses.asdict(self.params)

    @property
    def model(self):
        return self.params.model

    def call(self, audio_path, recording_id, camera):
        """Run the engine. Returns `(native, meta)`: the response, verbatim and
        JSON-able, and a dict that may carry `request_id`, `model_returned`,
        `audio_duration_s`, `chunked`, `notes`."""
        raise NotImplementedError

    def normalize(self, native):
        """`native` -> `{"language", "segments", "word_timing"?}`."""
        raise NotImplementedError

    def preflight(self, recording):
        """Checks that must pass before any audio is read."""

    def transcribe(self, recording_id, manifest_path=MANIFEST_PATH, audio_path=None):
        """Transcribe one recording's canonical audio (or `audio_path`, such as an
        excerpt of it). The recording must be in the manifest either way: that
        is where the cloud interlock lives."""
        recording = manifest.get_recording(recording_id, manifest_path)
        if recording is None:
            raise KeyError(f"recording_id {recording_id!r} not in {manifest_path}")
        self.preflight(recording)

        camera = manifest.ensure_audio_camera(recording_id, manifest_path)
        audio = Path(audio_path) if audio_path else _audio_path(recording_id, camera, manifest_path)
        if not audio.exists():
            raise FileNotFoundError(audio)

        started = datetime.datetime.now(datetime.timezone.utc)
        t0 = time.monotonic()
        native, meta = self.call(audio, recording_id, camera)
        wall_s = round(time.monotonic() - t0, 1)

        result = self.normalize(native)
        params = self.resolved_params()
        provenance = {
            "engine": self.engine,
            "kind": self.kind,
            "model": self.model,
            "model_returned": meta.get("model_returned"),
            "request_id": meta.get("request_id"),
            "params": params,
            "recording_id": recording_id,
            "camera": camera,
            "audio": _repo_relative(audio),
            "audio_duration_s": meta.get("audio_duration_s"),
            "started_at": started.isoformat(timespec="seconds"),
            "wall_s": wall_s,
            "software": _versions(self.packages),
        }
        if self.kind == "cloud":
            provenance["cloud_release_basis"] = recording.cloud_release_basis
        if meta.get("notes"):
            provenance["notes"] = meta["notes"]

        record = records.finalize_record(
            result, self.engine, word_timing=result.pop("word_timing", True),
            chunked=bool(meta.get("chunked")), provenance=provenance)
        problems = records.validate_record(record)
        if problems:
            raise ValueError(f"{self.engine} produced an invalid record: {problems[:5]}")
        return Transcription(recording_id, camera, self.engine, self.model, params, native, record)


class CloudTranscriber(Transcriber):
    kind = "cloud"
    env_keys = ()   # .env variables this vendor needs

    def preflight(self, recording):
        # The interlock (spec §9, plan 4.2): refuse before the audio is even located.
        if not recording.cloud_release:
            raise CloudReleaseError(
                f"recording {recording.recording_id} has cloud_release = 0: its audio may not "
                f"be sent to {self.engine}. Set cloud_release and cloud_release_basis in the "
                "manifest first.")
        load_env()
        missing = [k for k in self.env_keys if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"{self.engine}: set {', '.join(missing)} in .env")


def load_env():
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))


def _audio_path(recording_id, camera, manifest_path):
    video = manifest.get_video(recording_id, camera, manifest_path)
    if video is not None and video.audio_path:
        return ROOT / video.audio_path
    return DATA_DIR / "audio" / recording_id / f"{camera}.wav"


def _repo_relative(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _versions(packages):
    versions = {}
    for name in packages:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


# --- helpers for normalizers ------------------------------------------------

class SpeakerLabels:
    """Vendor labels ("A", "speaker_0", "2", ...) -> `SPEAKER_00` form, numbered in
    order of first appearance (spec §4 rule 3). None stays None."""

    def __init__(self):
        self._seen = {}

    def __call__(self, label):
        if label is None or label == "":
            return None
        if label not in self._seen:
            self._seen[label] = f"SPEAKER_{len(self._seen):02d}"
        return self._seen[label]


def word(text, start, end, score=None, speaker=None):
    """One normalized word. `score` stays None when the vendor gives no
    confidence (spec §4 rule 4); `speaker` is left off when there is none."""
    w = {"word": text, "start": start, "end": end, "score": score}
    if speaker is not None:
        w["speaker"] = speaker
    return w


def segments_from_words(words, max_gap_s=1.0):
    """Group normalized words into segments, breaking on a change of speaker or
    a silence longer than `max_gap_s`. For vendors that return words but no
    segments of their own."""
    segments = []
    for w in words:
        current = segments[-1] if segments else None
        if (current is None
                or w.get("speaker") != current["words"][-1].get("speaker")
                or (w["start"] is not None and current["end"] is not None
                    and w["start"] - current["end"] > max_gap_s)):
            current = {"start": w["start"], "end": w["end"], "words": []}
            segments.append(current)
        current["words"].append(w)
        if w["end"] is not None:
            current["end"] = w["end"]
    for s in segments:
        s["text"] = " ".join(w["word"] for w in s["words"])
        speakers = {w.get("speaker") for w in s["words"]} - {None}
        if len(speakers) == 1:
            s["speaker"] = speakers.pop()
    return segments
