"""multidata-local: clinical-encounter video -> transcripts, prosody, 2D pose.

Modules, by role (stages run through `scripts/run_stage.py <stage>`; the
conda env each needs is in brackets; details in docs/pipeline.md):

Bookkeeping
  manifest     the SQLite registry every stage reads and updates   [any env]
  models       Case / Video / Camera rows of the manifest           [any env]
  paths        repo-relative locations (data/, manifest.sqlite)     [any env]
  ingest       check and fingerprint a raw video file               [any env]
  casematch    which case a raw video belongs to (time + room)      [any env]

Speech stages                                                        [md-speech]
  audio        16 kHz mono WAV from the case's audio camera
  asr          transcript with word timing and speakers (--engine)
  diarize      who spoke when (pyannote) -> RTTM
  elan         ELAN .eaf file for annotators
  prosody      pitch, loudness, voice quality; per-speaker summary
  sync         camera offsets (from audio) + per-frame timestamps

Video stages                                                         [md-pose]
  pose         2D body keypoints (HALPE_26) per camera
  views        when each PTZ camera moves, and which view it holds

ASR benchmarking (see docs/asr_provider_spec.md)                     [md-speech]
  normalize, redact, records, providers, glossary, bench_runs

Helpers
  device          best GPU/CPU choice for the speech models
  hf_auth         Hugging Face token for pyannote
  speaker_traits  rough vocal-register bucket, for sampling only
  acoustics       old CSV export of prosody features (kept for notebooks)
"""
