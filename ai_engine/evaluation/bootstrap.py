"""Clip-level (cluster) bootstrap for queue-length errors, per ``docs/evaluation_protocol.md``.

Clips are the resampling unit: clips of a split are drawn with replacement and the metric is
recomputed pooled over all annotated frames of the drawn clips (10,000 resamples, seed 20261116,
95 % percentile interval). Because the draws depend only on the number of clips and the seed, two
configurations scored on the same clips are resampled identically (paired comparison).

A per-frame bootstrap (every frame its own cluster) is also provided. It ignores the correlation
between frames of one clip, so its intervals are too narrow: it is labelled optimistic and never
decides anything.

The resampling machinery is shared with ``day_bootstrap`` (a cluster is a day there, a clip here).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from ai_engine.evaluation.day_bootstrap import (
    LEVEL,
    N_RESAMPLES,
    bootstrap_stat,
    day_sums,
    resample_counts,
)

SEED = 20_261_116
METRICS = ("mae", "bias", "within_one_rate")
OPTIMISTIC_LABEL = "optimistic (ignores within-clip correlation)"

# One frame: (time_s, error = predicted - truth, rounded_error)
Frame = tuple[float, float, int]
FramesByClip = Mapping[str, Sequence[Frame]]


def _columns(frames: Sequence[Frame]) -> np.ndarray:
    """(n, 3) columns: |error|, error, 1 if |rounded error| <= 1."""
    arr = np.asarray(frames, dtype=float).reshape(-1, 3)
    return np.stack([np.abs(arr[:, 1]), arr[:, 1], (np.abs(arr[:, 2]) <= 1).astype(float)], axis=1)


def _stack(frames_by_clip: FramesByClip, clips: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame columns and the clip index of each frame."""
    cols, idx = [], []
    for i, clip in enumerate(clips):
        frames = frames_by_clip.get(clip, [])
        cols.append(_columns(frames))
        idx.append(np.full(len(frames), i))
    if not cols:
        return np.zeros((0, 3)), np.zeros(0, dtype=int)
    return np.vstack(cols), np.concatenate(idx).astype(int)


def _unit_index(clip_idx: np.ndarray, unit: str) -> np.ndarray:
    if unit == "clip":
        return clip_idx
    if unit == "frame":
        return np.arange(len(clip_idx))
    raise ValueError(f"unit must be 'clip' or 'frame', got {unit!r}")


def _interval(point_low_high: tuple[float, float, float]) -> dict[str, float]:
    point, low, high = point_low_high
    return {"point": point, "low": low, "high": high}


def _n_units(clips: Sequence[str], clip_idx: np.ndarray, unit: str) -> int:
    return len(clips) if unit == "clip" else len(clip_idx)


def pooled_intervals(
    frames_by_clip: FramesByClip,
    clips: Sequence[str],
    unit: str = "clip",
    n_resamples: int = N_RESAMPLES,
    seed: int = SEED,
    level: float = LEVEL,
) -> dict[str, Any]:
    """MAE, bias and within-±1 pooled over the frames of ``clips`` with bootstrap intervals."""
    cols, clip_idx = _stack(frames_by_clip, clips)
    n_units = _n_units(clips, clip_idx, unit)
    idx = _unit_index(clip_idx, unit)
    out: dict[str, Any] = {
        "unit": unit,
        "n_clips": len(clips),
        "n_frames": len(cols),
        "n_resamples": n_resamples,
        "seed": seed,
        "level": level,
    }
    if unit == "frame":
        out["label"] = OPTIMISTIC_LABEL
    if len(cols) == 0:
        return out | {m: {"point": None, "low": None, "high": None} for m in METRICS}
    stats = day_sums(idx, [cols[:, 0], cols[:, 1], cols[:, 2], np.ones(len(cols))], n_units)
    counts = resample_counts(n_units, n_resamples, seed)
    for k, name in enumerate(METRICS):
        out[name] = _interval(bootstrap_stat(stats, counts, lambda t, k=k: t[:, k] / t[:, 3]))
    return out


def align_frames(a: FramesByClip, b: FramesByClip, clips: Sequence[str]) -> tuple[dict, dict, int]:
    """Restrict both sets to frames present in both (same clip, same time); also the dropped count."""
    out_a: dict[str, list[Frame]] = {}
    out_b: dict[str, list[Frame]] = {}
    dropped = 0
    for clip in clips:
        ma = {round(f[0], 6): f for f in a.get(clip, [])}
        mb = {round(f[0], 6): f for f in b.get(clip, [])}
        shared = sorted(set(ma) & set(mb))
        dropped += len(set(ma) | set(mb)) - len(shared)
        out_a[clip] = [ma[t] for t in shared]
        out_b[clip] = [mb[t] for t in shared]
    return out_a, out_b, dropped


def paired_difference(
    new: FramesByClip,
    current: FramesByClip,
    clips: Sequence[str],
    unit: str = "clip",
    n_resamples: int = N_RESAMPLES,
    seed: int = SEED,
    level: float = LEVEL,
) -> dict[str, Any]:
    """Interval of (new - current) on each pooled metric; both resampled with the same draws."""
    a, b, dropped = align_frames(new, current, clips)
    ca, idx_a = _stack(a, clips)
    cb, _ = _stack(b, clips)
    n_units = _n_units(clips, idx_a, unit)
    idx = _unit_index(idx_a, unit)
    out: dict[str, Any] = {
        "unit": unit,
        "n_clips": len(clips),
        "n_frames": len(ca),
        "n_frames_dropped_unaligned": dropped,
        "n_resamples": n_resamples,
        "seed": seed,
        "level": level,
        "difference": "new - current",
    }
    if unit == "frame":
        out["label"] = OPTIMISTIC_LABEL
    if len(ca) == 0:
        return out | {m: {"point": None, "low": None, "high": None, "half_width": None} for m in METRICS}
    ones = np.ones(len(ca))
    stats = day_sums(idx, [ca[:, 0], ca[:, 1], ca[:, 2], cb[:, 0], cb[:, 1], cb[:, 2], ones], n_units)
    counts = resample_counts(n_units, n_resamples, seed)
    for k, name in enumerate(METRICS):
        iv = _interval(bootstrap_stat(stats, counts, lambda t, k=k: (t[:, k] - t[:, k + 3]) / t[:, 6]))
        iv["half_width"] = (iv["high"] - iv["low"]) / 2
        out[name] = iv
    return out
