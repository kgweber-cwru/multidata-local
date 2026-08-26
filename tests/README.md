# tests/

`python -m pytest` from the repo root (any env with `pytest` — currently only
`md-speech`, see `env/speech.yml`).

**Every test here is stdlib/pyyaml/torch-only, and none touches a real model
weight, GPU, network, or the manifest database.** `torch` shows up only in
`test_device.py`, mocking `torch.cuda.is_available`/`torch.backends.mps.is_
available` rather than needing real GPU hardware; that file `importorskip`s
`torch` so the rest of the suite still collects and passes in an env without
it. Everything else is stdlib or pyyaml. That's a deliberate boundary, not an
oversight:

- The modules under test in Phase 1
  ([docs/asr_provider_implementation_plan.md](../docs/asr_provider_implementation_plan.md))
  — `normalize.py`, `redact.py`, `records.py`, `providers.py`, `glossary.py`
  — are pure by design, specifically so they *can* be tested this fast and
  this cheaply. If a change to one of them requires a model or a GPU to test,
  that's a sign the change put logic in the wrong module.
- `asr.py`'s heavy imports (`whisperx`, `faster_whisper`, `torch`) are all
  deferred inside functions, not at module level — so `test_asr_dispatch.py`
  can import `multidata.asr` and test its *dispatch logic* (which engine gets
  called with which kwargs) by monkeypatching `asr.ENGINES` entries with
  stubs, without whisperx or torch installed at all. Keep new asr.py code
  inside that lazy-import pattern or this stops being possible.
- `test_device.py` mocks `torch.cuda`/`torch.backends.mps` rather than
  requiring real GPU hardware — this is the actual verification strategy for
  "does this port to an NVIDIA box," short of owning one. It proves the
  *selection logic* picks cuda/float16 when CUDA is reported available;
  it cannot prove the real hardware behaves as reported.

**What's deliberately not tested here:** whether a real Whisper model
transcribes correctly, whether pyannote diarizes correctly, whether a cloud
adapter's HTTP request is well-formed. Those need real audio, real models, or
real (billed) API calls — they're integration-level concerns for Phase 3+,
not unit tests, and they don't belong in a suite meant to run in seconds on
every change.
