"""What every transcriber shares, local or cloud (docs/asr_provider_spec.md §4,
implementation plan 3.1).

A transcriber starts from a `recording_id`, takes the parameters its vendor's
models accept, and returns a `Transcription`: the vendor's response verbatim
(`native`), the normalized record every consumer reads (`record`), and every
parameter that produced them (`params`, also in `record["provenance"]`).
`save()` writes both views to disk and indexes them in the manifest.

Subclasses supply three things: a `Params` dataclass (so a misspelled or
foreign parameter fails at construction, naming itself), `call()` (send the
audio, return the response) and `normalize()` (response -> segments). The
manifest lookup, the cloud interlock, chunking, speaker source, stamping and
validation live here, once (chunking and speakers: `chunking.py`).

Every transcriber also takes `speakers`: "engine" keeps the engine's own
speaker labels, "pipeline" replaces them with the diarize stage's.
"""
import dataclasses
import datetime
import importlib.metadata
import json
import os
import tempfile
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

    def save(self, out_dir=None, overwrite=False, manifest_path=MANIFEST_PATH):
        """Write `<camera>_<engine>_<model>.json` (normalized) and `.raw.json`
        (native) to `out_dir`, default `data/transcripts/<recording_id>/`
        (spec §7), and register them in the manifest's `transcripts` table.
        Refuses to replace an existing file unless `overwrite`: the default
        directory also holds the pipeline's own transcripts, and seeded
        annotation kits were built from some of them."""
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
        prov = self.record.get("provenance", {})
        manifest.record_transcript(
            self.recording_id, self.camera, self.engine, self.model,
            _repo_relative(normalized), _repo_relative(raw), params=self.params,
            path=manifest_path, kind=prov.get("kind") or "",
            model_returned=prov.get("model_returned") or "",
            request_id=prov.get("request_id") or "", audio=prov.get("audio") or "",
            audio_duration_s=prov.get("audio_duration_s"),
            started_at=prov.get("started_at") or "", wall_s=prov.get("wall_s"))
        return normalized, raw


SPEAKER_SOURCES = ("engine", "pipeline")


class Transcriber:
    engine = None        # registry name, as in providers.yaml
    kind = "local"       # "local" | "cloud"
    Params = None        # a dataclass of everything this engine accepts; a
                         # `chunk_s` field (with `chunk_overlap_s`) turns on chunking
    packages = ()        # distributions whose versions go into provenance

    def __init__(self, speakers="engine", **params):
        self.speakers = speakers
        self.params = self.Params(**params)   # an unknown name is a TypeError here
        self.check_speakers()
        self.check_params()

    def check_speakers(self):
        if self.speakers not in SPEAKER_SOURCES:
            raise ValueError(f"speakers must be one of {SPEAKER_SOURCES}")

    def check_params(self):
        """Raise ValueError for combinations the vendor rejects."""

    def resolved_params(self):
        return {**dataclasses.asdict(self.params), "speakers": self.speakers}

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

    def run(self, audio, recording_id, camera, excerpt=False):
        """`call` + `normalize`, in planned pieces when the audio is longer than
        `chunk_s` (`chunking.py`) -> (native, meta, result). A chunked run's
        `native` is `{"chunks": [{index, offset_s, duration_s, response}]}`."""
        from . import chunking

        chunk_s = getattr(self.params, "chunk_s", None)
        total = _duration(audio)
        if not chunk_s or (total or 0) <= chunk_s:
            native, meta = self.call(audio, recording_id, camera)
            return native, meta, self.normalize(native)
        if excerpt:
            raise ValueError(f"audio over {chunk_s} s is cut where the diarize stage's turns "
                             "say, which are timed on the whole recording; send an excerpt "
                             "shorter than that")

        spans = chunking.plan(_pipeline_turns(recording_id, camera), total, chunk_s,
                              self.params.chunk_overlap_s)
        chunks, metas, results = [], [], []
        with tempfile.TemporaryDirectory() as tmp:
            for index, (piece, offset_s, duration_s) in enumerate(
                    chunking.cut(audio, Path(tmp), spans)):
                native, meta = self.call(piece, recording_id, camera)
                chunks.append({"index": index, "offset_s": offset_s,
                               "duration_s": duration_s, "response": native})
                metas.append(meta)
                results.append((offset_s, duration_s, self.normalize(native)))
        result, merge_notes = chunking.merge(results)
        notes = [f"{len(chunks)} pieces overlapping {self.params.chunk_overlap_s} s"]
        notes += merge_notes + [m["notes"] for m in metas if m.get("notes")]
        meta = {"request_id": ";".join(str(m.get("request_id")) for m in metas),
                "model_returned": metas[0].get("model_returned"),
                "chunked": True, "notes": "; ".join(notes)}
        return {"chunks": chunks}, meta, result

    def assign_pipeline_speakers(self, result, audio, recording_id, camera):
        """`result` with the diarize stage's speaker labels (`speakers="pipeline"`)."""
        from . import chunking

        return chunking.assign_speakers(result, _pipeline_turns(recording_id, camera))

    def transcribe(self, recording_id, manifest_path=MANIFEST_PATH, audio_path=None):
        """Transcribe one recording's canonical audio (or `audio_path`, such as an
        excerpt of it). The recording must be in the manifest either way: that
        is where the cloud interlock lives."""
        recording = manifest.get_recording(recording_id, manifest_path)
        if recording is None:
            raise KeyError(f"recording_id {recording_id!r} not in {manifest_path}")
        self.preflight(recording)

        camera = manifest.ensure_audio_camera(recording_id, manifest_path)
        canonical = _audio_path(recording_id, camera, manifest_path)
        audio = Path(audio_path) if audio_path else canonical
        if not audio.exists():
            raise FileNotFoundError(audio)
        excerpt = audio.resolve() != canonical.resolve()
        if self.speakers == "pipeline" and excerpt:
            # The diarize stage's turns are timed on the whole recording.
            raise ValueError("speakers='pipeline' needs the recording's own audio, "
                             "not an excerpt; pass speakers='engine'")

        started = datetime.datetime.now(datetime.timezone.utc)
        t0 = time.monotonic()
        native, meta, result = self.run(audio, recording_id, camera, excerpt)
        if self.speakers == "pipeline":
            result = self.assign_pipeline_speakers(result, audio, recording_id, camera)
        wall_s = round(time.monotonic() - t0, 1)

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
            "audio_duration_s": meta.get("audio_duration_s") or _duration(audio),
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


def _pipeline_turns(recording_id, camera):
    """The diarize stage's turns [(start_s, end_s, speaker)] for one recording."""
    from multidata.prosody import read_rttm

    from . import chunking

    rttm = chunking.pipeline_rttm(recording_id, camera)
    if not rttm.exists():
        raise FileNotFoundError(f"{rttm} -- run the audio and diarize stages first")
    return read_rttm(rttm)


def _repo_relative(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _duration(audio):
    try:
        import soundfile

        return round(soundfile.info(str(audio)).duration, 3)
    except Exception:
        return None


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
