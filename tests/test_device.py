"""Device-selection logic, mocked (no real GPU needed or assumed) -- this is
the actual verification strategy for NVIDIA portability short of owning the
hardware. It proves the *selection logic* picks cuda/float16 when CUDA is
reported available; it can't prove the real hardware behaves as reported.
See tests/README.md."""
import pytest

torch = pytest.importorskip("torch")  # md-speech has it; skip cleanly elsewhere

from multidata import device  # noqa: E402


class TestBestTorchDevice:
    """mps > cuda > cpu, for the aligner/diarizer (device.py's own split)."""

    def test_prefers_mps_when_available(self, monkeypatch):
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert device.best_torch_device() == "mps"

    def test_falls_back_to_cuda_without_mps(self, monkeypatch):
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert device.best_torch_device() == "cuda"

    def test_falls_back_to_cpu_with_neither(self, monkeypatch):
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert device.best_torch_device() == "cpu"


class TestBestCt2Device:
    """cuda > cpu, and -- the whole point of splitting this from
    best_torch_device -- NEVER mps, regardless of what mps reports."""

    def test_prefers_cuda_when_available(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert device.best_ct2_device() == "cuda"

    def test_falls_back_to_cpu_without_cuda(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert device.best_ct2_device() == "cpu"

    def test_ignores_mps_even_when_available(self, monkeypatch):
        # This is the Mac-today / NVIDIA-tomorrow portability contract: ct2
        # must never pick mps just because best_torch_device() would.
        monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert device.best_ct2_device() == "cpu"


class TestBestCt2ComputeType:
    def test_cuda_gets_float16(self):
        assert device.best_ct2_compute_type("cuda") == "float16"

    def test_cpu_gets_int8(self):
        assert device.best_ct2_compute_type("cpu") == "int8"

    def test_unrecognized_device_falls_back_to_int8(self):
        # Portable-and-safe default rather than raising -- int8 runs
        # everywhere ctranslate2 runs.
        assert device.best_ct2_compute_type("something_new") == "int8"


class TestNvidiaBoxSimulation:
    """The end-to-end claim: drop this code on a box that reports CUDA
    available, and the ct2 path resolves to cuda/float16 with no edits."""

    def test_simulated_nvidia_box_resolves_to_cuda_float16(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        chosen_device = device.best_ct2_device()
        chosen_compute = device.best_ct2_compute_type(chosen_device)
        assert (chosen_device, chosen_compute) == ("cuda", "float16")

    def test_simulated_mac_resolves_to_cpu_int8(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        chosen_device = device.best_ct2_device()
        chosen_compute = device.best_ct2_compute_type(chosen_device)
        assert (chosen_device, chosen_compute) == ("cpu", "int8")
