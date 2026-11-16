"""Day-level cluster bootstrap (minimal version).

This is a minimal version added for the Anonymous Bank evaluation because fix-pass session 2's
bootstrap has not landed. Days are the resampling unit (calls within a day are correlated):
days are drawn with replacement within a split, 10,000 resamples, seed 20261113, 95 % percentile
intervals. The same seed and number of days always give the same resampled days, so two methods
scored on one split are resampled identically (paired comparison).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

SEED = 20_261_113
N_RESAMPLES = 10_000
LEVEL = 0.95

Interval = tuple[float, float, float]  # point, low, high


def resample_counts(n_days: int, n_resamples: int = N_RESAMPLES, seed: int = SEED) -> np.ndarray:
    """(n_resamples, n_days) matrix: how often each day appears in each resample."""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_days, size=(n_resamples, n_days))
    counts = np.zeros((n_resamples, n_days), dtype=np.float64)
    for r in range(n_resamples):
        counts[r] = np.bincount(idx[r], minlength=n_days)
    return counts


def day_sums(day_idx: np.ndarray, columns: list[np.ndarray], n_days: int) -> np.ndarray:
    """(n_days, k) per-day sums of k per-call columns."""
    return np.stack(
        [np.bincount(day_idx, weights=np.asarray(c, dtype=float), minlength=n_days) for c in columns],
        axis=1,
    )


def percentile_interval(samples: np.ndarray, level: float = LEVEL) -> tuple[float, float]:
    tail = (1 - level) / 2 * 100
    lo, hi = np.nanpercentile(samples, [tail, 100 - tail])
    return float(lo), float(hi)


def bootstrap_stat(stats: np.ndarray, counts: np.ndarray, fn: Callable[[np.ndarray], np.ndarray]) -> Interval:
    """Interval for ``fn`` of the summed per-day statistics (``fn`` maps (R, k) -> (R,))."""
    with np.errstate(divide="ignore", invalid="ignore"):
        point = float(fn(stats.sum(axis=0, keepdims=True))[0])
        samples = fn(counts @ stats)
    if np.isnan(samples).all():
        return point, float("nan"), float("nan")
    lo, hi = percentile_interval(samples)
    return point, lo, hi


def bootstrap_mean(values: np.ndarray, day_idx: np.ndarray, counts: np.ndarray) -> Interval:
    stats = day_sums(day_idx, [values, np.ones(len(values))], counts.shape[1])
    return bootstrap_stat(stats, counts, lambda t: t[:, 0] / t[:, 1])


def bootstrap_rmse(errors: np.ndarray, day_idx: np.ndarray, counts: np.ndarray) -> Interval:
    stats = day_sums(day_idx, [errors**2, np.ones(len(errors))], counts.shape[1])
    return bootstrap_stat(stats, counts, lambda t: np.sqrt(t[:, 0] / t[:, 1]))


def bootstrap_median(values: np.ndarray, day_idx: np.ndarray, counts: np.ndarray) -> Interval:
    """Median of the pooled calls of the resampled days (weighted by day multiplicity)."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return float("nan"), float("nan"), float("nan")
    order = np.argsort(values, kind="stable")
    v = values[order]
    d = day_idx[order]
    samples = np.full(counts.shape[0], np.nan)
    for r in range(counts.shape[0]):
        cw = np.cumsum(counts[r][d])
        total = cw[-1]
        if total <= 0:
            continue
        k1 = np.searchsorted(cw, (total + 1) // 2)
        k2 = np.searchsorted(cw, total // 2 + 1)
        samples[r] = (v[k1] + v[k2]) / 2
    lo, hi = percentile_interval(samples)
    return float(np.median(values)), lo, hi


def bootstrap_mean_difference(
    a: np.ndarray, b: np.ndarray, day_idx: np.ndarray, counts: np.ndarray
) -> Interval:
    """Paired: mean(a) - mean(b) over the same calls, same resampled days."""
    stats = day_sums(day_idx, [a, b, np.ones(len(a))], counts.shape[1])
    return bootstrap_stat(stats, counts, lambda t: (t[:, 0] - t[:, 1]) / t[:, 2])
