"""multidata-local pipeline package.

Stage modules (see multidata_local_pipeline.md):
  manifest   -> the video registry that drives resumable batch runs (§10)
  ingest     -> validate + fingerprint raw video (§4)
  audio      -> extract 16 kHz mono WAV for ASR/diarization (§5)
  asr        -> transcription + word timing, per --engine (§6)
  diarize    -> pyannote speaker diarization -> RTTM (§6)
  elan       -> build .eaf from transcripts (§7)
  pose       -> rtmlib RTMPose extraction -> (M, T, V, C) .pkl (§9)

Analysis helpers (not manifest stages):
  kinematics -> rule-based pose features: torso angle, hand velocity
  acoustics  -> Praat/parselmouth pitch, intensity, harmonicity, formants

Env split: `pose` and `kinematics` need md-pose; everything else md-speech.
`manifest` is stdlib-only so it imports in both.
"""
