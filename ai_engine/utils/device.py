"""Hardware detection and device synchronization."""

from __future__ import annotations

import torch

SUPPORTED_DEVICES = ("auto", "cpu", "mps", "cuda")


class DeviceUnavailableError(RuntimeError):
    """Raised when an explicitly requested device cannot be used."""


def cuda_available() -> bool:
    """Return True only when CUDA is genuinely usable."""
    return torch.cuda.is_available()


def mps_available() -> bool:
    """Return True only when Apple MPS is built into torch and usable."""
    return torch.backends.mps.is_built() and torch.backends.mps.is_available()


def select_device(requested: str = "auto") -> str:
    """Resolve a requested device name to one that is actually available.

    ``auto`` picks CUDA, then MPS, then CPU. An explicit request for an
    unavailable accelerator raises :class:`DeviceUnavailableError` rather
    than silently falling back, so results never claim acceleration that
    was not used.
    """
    requested = requested.lower().strip()
    if requested not in SUPPORTED_DEVICES:
        raise ValueError(
            f"Unknown device '{requested}'. Use one of: {', '.join(SUPPORTED_DEVICES)}"
        )

    if requested == "auto":
        if cuda_available():
            return "cuda"
        if mps_available():
            return "mps"
        return "cpu"

    if requested == "cuda" and not cuda_available():
        raise DeviceUnavailableError("CUDA was requested but is not available.")
    if requested == "mps" and not mps_available():
        raise DeviceUnavailableError(
            "MPS was requested but is not available on this machine/PyTorch build."
        )
    return requested


def synchronize(device: str) -> None:
    """Block until queued GPU work finishes, so timings are not misleading."""
    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()
