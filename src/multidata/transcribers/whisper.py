"""The local Whisper engines (`asr.ENGINES`) behind the same interface as the
cloud vendors.

Parameters are whatever the engine function takes (`model_name`, `prompt`,
`condition_on_previous_text`, ...), plus the diarization settings
`asr.transcribe` takes. Defaults come from the function's own signature, so
`resolved_params()` is exactly what ran.

`speakers="pipeline"` (the default here, unlike the cloud engines) takes
speaker labels from the diarize stage's RTTM,
`data/diarization/<recording>/<camera>.rttm`, written if missing -- the same
labels the pipeline's transcript, prosody and ELAN files use.
`speakers="engine"` lets the whisperx family use its own pyannote run
(faster_whisper still gets a fresh pyannote run merged in, without an RTTM
being kept).

There is no vendor response distinct from the result here, so `native` is the
engine's output as `asr.transcribe` returned it, before stamping.
"""
import copy
import inspect

from multidata import asr
from multidata.paths import DATA_DIR

from .base import Transcriber

DIARIZE_PARAMS = {"speakers": "pipeline", "diarize_device": None,
                  "num_speakers": None, "min_speakers": None, "max_speakers": None}
PACKAGES = {"faster_whisper": ("faster-whisper", "pyannote.audio", "whisperx"),
            "whisperx": ("whisperx", "faster-whisper", "pyannote.audio"),
            "whisperx_disfluent": ("whisperx", "faster-whisper", "pyannote.audio")}


class WhisperTranscriber(Transcriber):
    kind = "local"

    def __init__(self, engine, **params):
        if engine not in asr.ENGINES:
            raise ValueError(f"unknown local engine {engine!r}; pick one of {sorted(asr.ENGINES)}")
        self.engine = engine
        self.packages = PACKAGES.get(engine, ())
        defaults = engine_defaults(engine)
        unknown = set(params) - set(defaults)
        if unknown:
            raise TypeError(f"{engine} takes no parameter(s) {sorted(unknown)}; "
                            f"it takes {sorted(defaults)}")
        self.params = {**defaults, **params}
        self.speakers = self.params["speakers"]
        self.check_speakers()

    def resolved_params(self):
        return dict(self.params)

    @property
    def model(self):
        return self.params["model_name"]

    def call(self, audio_path, recording_id, camera):
        kwargs = dict(self.params)
        rttm = None
        if kwargs.pop("speakers") == "pipeline":
            rttm = DATA_DIR / "diarization" / recording_id / f"{camera}.rttm"
            rttm.parent.mkdir(parents=True, exist_ok=True)
        result = asr.transcribe(audio_path, engine=self.engine, diarize_rttm=rttm, **kwargs)
        notes = f"speaker labels from {rttm.relative_to(DATA_DIR.parent)}" if rttm else None
        return copy.deepcopy(result), {"model_returned": self.model, "notes": notes}

    def assign_pipeline_speakers(self, result, audio, recording_id, camera):
        return result   # asr.transcribe already took them from the RTTM, in `call`

    def normalize(self, native):
        segments = []
        for s in native.get("segments", []):
            seg = {k: v for k, v in s.items() if k != "words"}
            # whisperx's aligner can't place some tokens (digits, symbols); they
            # come back without times, and stay that way (spec §4 rule 2).
            seg["words"] = [{**w, "start": w.get("start"), "end": w.get("end")}
                            for w in s.get("words", [])]
            segments.append(seg)
        return {"language": native.get("language", asr.DEFAULT_LANGUAGE), "segments": segments}


def engine_defaults(engine):
    """{parameter: default} for one local engine, from its function signature."""
    sig = inspect.signature(asr.ENGINES[engine])
    defaults = {name: p.default for name, p in sig.parameters.items()
                if name != "audio_path" and p.default is not inspect.Parameter.empty}
    defaults.pop("diarize", None)   # asr.transcribe sets it
    return {**defaults, **DIARIZE_PARAMS}
