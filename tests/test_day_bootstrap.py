import numpy as np

from ai_engine.evaluation.day_bootstrap import (
    SEED,
    bootstrap_mean,
    bootstrap_mean_difference,
    bootstrap_median,
    bootstrap_rmse,
    resample_counts,
)


def test_counts_shape_and_seed_stability():
    c1 = resample_counts(7, 200)
    c2 = resample_counts(7, 200)
    assert c1.shape == (200, 7) and (c1.sum(axis=1) == 7).all()
    assert np.array_equal(c1, c2)
    assert not np.array_equal(c1, resample_counts(7, 200, seed=SEED + 1))
    assert SEED == 20261113


def test_two_day_known_answer():
    # Day A all 0, day B all 10: resample mean is 0, 5 or 10 with prob 1/4, 1/2, 1/4.
    vals = np.array([0.0, 0.0, 10.0, 10.0, 10.0])
    day = np.array([0, 0, 1, 1, 1])
    counts = resample_counts(2, 10_000)
    point, lo, hi = bootstrap_mean(vals, day, counts)
    assert abs(point - 6.0) < 1e-12
    assert 0 <= lo <= 3 and 8 <= hi <= 10
    pure = np.array([4.0, 4.0, 4.0])
    p, lo, hi = bootstrap_mean(pure, np.array([0, 1, 2]), resample_counts(3, 500))
    assert p == lo == hi == 4.0


def test_median_rmse_and_paired():
    vals = np.array([1.0, 2.0, 3.0, 4.0, 100.0, 5.0])
    day = np.array([0, 0, 1, 1, 2, 2])
    counts = resample_counts(3, 2000)
    p, lo, hi = bootstrap_median(vals, day, counts)
    assert p == 3.5 and 1.0 <= lo <= hi <= 100.0
    p, lo, hi = bootstrap_rmse(np.array([3.0, 4.0]), np.array([0, 1]), resample_counts(2, 500))
    assert abs(p - np.sqrt(12.5)) < 1e-12 and 3 <= lo <= hi <= 4
    a = np.array([5.0, 7.0, 9.0, 11.0])
    b = a - 2.0
    d = bootstrap_mean_difference(a, b, np.array([0, 0, 1, 1]), resample_counts(2, 500))
    assert d == (2.0, 2.0, 2.0)
