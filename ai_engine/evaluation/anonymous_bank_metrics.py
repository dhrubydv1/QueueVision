"""Metrics and breakdowns for the Anonymous Bank replay (protocol v1).

Only AGENT calls enter any error metric; HANG calls are censored and only counted.
Every interval is a day-level cluster bootstrap (``day_bootstrap``).
"""

from __future__ import annotations

import numpy as np

from ai_engine.data.anonymous_bank_replay import Prediction
from ai_engine.evaluation import day_bootstrap as bs

METHODS = ("predictor", "les", "recent_mean", "regression")
QL_BANDS = [("0", 0, 0), ("1-2", 1, 2), ("3-5", 3, 5), ("6-10", 6, 10), (">10", 11, 10**9)]


def months_split(month: int) -> str:
    return "train" if month <= 8 else "validation" if month <= 10 else "test"


def iv(t: bs.Interval) -> dict:
    return {"value": t[0], "lo": t[1], "hi": t[2]}


class Frame:
    """Arrays for one (queue, split): one row per call, HANG included."""

    def __init__(self, preds: list[Prediction], reg: tuple[float, float]):
        dates = sorted({p.date for p in preds})
        self.n_days = len(dates)
        pos = {d: i for i, d in enumerate(dates)}
        nan = float("nan")

        def arr(f):
            return np.array([f(p) for p in preds], dtype=float)

        self.day = np.array([pos[p.date] for p in preds], dtype=int)
        self.agent = np.array([p.outcome == "AGENT" for p in preds], dtype=bool)
        self.wait = arr(lambda p: p.wait)
        self.ql = arr(lambda p: p.queue_length)
        self.hour = np.array([(p.q_start // 3600) % 24 for p in preds], dtype=int)
        self.prio = np.array([p.priority for p in preds], dtype=int)
        self.type = np.array([p.type for p in preds], dtype=object)
        self.low = arr(lambda p: nan if p.low is None else p.low)
        self.high = arr(lambda p: nan if p.high is None else p.high)
        a, b = reg
        self.m = {
            "predictor": arr(lambda p: nan if p.pred is None else p.pred),
            "les": arr(lambda p: nan if p.les is None else p.les),
            "recent_mean": arr(lambda p: nan if p.recent_mean is None else p.recent_mean),
            "regression": arr(lambda p: a + b * (p.queue_length + 1)),
        }
        self.counts = bs.resample_counts(self.n_days) if self.n_days else None

    def mask(self, method: str) -> np.ndarray:
        return self.agent & np.isfinite(self.m[method])


def method_metrics(fr: Frame, method: str) -> dict:
    cov_mask = fr.mask(method)
    n_agent = int(fr.agent.sum())
    out: dict = {"n_agent": n_agent, "n_covered": int(cov_mask.sum())}
    if fr.n_days == 0:
        return out
    c = fr.counts
    out["coverage"] = iv(
        bs.bootstrap_stat(
            bs.day_sums(fr.day, [cov_mask, fr.agent], fr.n_days), c, lambda t: t[:, 0] / t[:, 1]
        )
    )
    d = fr.day[cov_mask]
    err = fr.m[method][cov_mask] - fr.wait[cov_mask]
    if len(err) == 0:
        return out
    ae = np.abs(err)
    out["mae_s"] = iv(bs.bootstrap_mean(ae, d, c))
    out["rmse_s"] = iv(bs.bootstrap_rmse(err, d, c))
    out["median_abs_error_s"] = iv(bs.bootstrap_median(ae, d, c))
    out["bias_s"] = iv(bs.bootstrap_mean(err, d, c))
    out["within_30s"] = iv(bs.bootstrap_mean((ae <= 30).astype(float), d, c))
    out["within_60s"] = iv(bs.bootstrap_mean((ae <= 60).astype(float), d, c))
    if method == "predictor":
        lo, hi, w = fr.low[cov_mask], fr.high[cov_mask], fr.wait[cov_mask]
        inside = ((w >= lo) & (w <= hi)).astype(float)
        out["range_coverage"] = iv(bs.bootstrap_mean(inside, d, c))
    return out


def hang_report(fr: Frame) -> dict:
    hang = ~fr.agent
    pred = fr.m["predictor"]
    with_pred = hang & np.isfinite(pred)
    return {
        "n_hang": int(hang.sum()),
        "n_hang_with_prediction": int(with_pred.sum()),
        "n_hung_up_after_longer_than_predicted": int((with_pred & (fr.wait > pred)).sum()),
    }


def paired_vs_baselines(fr: Frame) -> dict:
    """MAE(predictor) - MAE(baseline) on calls covered by both, same resampled days."""
    out = {}
    for base in ("les", "recent_mean", "regression"):
        mk = fr.mask("predictor") & fr.mask(base)
        if fr.n_days == 0 or not mk.any():
            continue
        a = np.abs(fr.m["predictor"][mk] - fr.wait[mk])
        b = np.abs(fr.m[base][mk] - fr.wait[mk])
        out[base] = {
            "n_common": int(mk.sum()),
            "mae_diff_s": iv(bs.bootstrap_mean_difference(a, b, fr.day[mk], fr.counts)),
        }
    return out


def _cell(fr: Frame, method: str, sel: np.ndarray) -> dict:
    mk = fr.mask(method) & sel
    out = {"n": int(mk.sum()), "n_agent": int((fr.agent & sel).sum())}
    if not mk.any():
        return out
    err = fr.m[method][mk] - fr.wait[mk]
    d = fr.day[mk]
    out["mae_s"] = iv(bs.bootstrap_mean(np.abs(err), d, fr.counts))
    out["bias_s"] = iv(bs.bootstrap_mean(err, d, fr.counts))
    out["within_60s"] = iv(bs.bootstrap_mean((np.abs(err) <= 60).astype(float), d, fr.counts))
    return out


def breakdowns(fr: Frame, method: str) -> dict:
    res: dict = {"hour": {}, "queue_length_band": {}, "priority": {}, "type": {}}
    if fr.n_days == 0:
        return res
    for h in range(24):
        res["hour"][str(h)] = _cell(fr, method, fr.hour == h)
    for name, lo, hi in QL_BANDS:
        res["queue_length_band"][name] = _cell(fr, method, (fr.ql >= lo) & (fr.ql <= hi))
    res["priority"]["regular_0_1"] = _cell(fr, method, fr.prio < 2)
    res["priority"]["priority_2"] = _cell(fr, method, fr.prio == 2)
    for t in sorted(set(fr.type)):
        res["type"][t] = _cell(fr, method, fr.type == t)
    return res


def little_law(calls_by_day: dict, n_days: int) -> dict:
    """Per hour of day over all days: time-average queue length vs lambda * mean wait.

    ``calls_by_day`` maps date -> calls (queued AGENT and HANG of the primary queue). Queue time
    is clipped to the day's own 0-24 h timeline.
    """
    qsec = np.zeros(24)
    arr = np.zeros(24)
    wsum = np.zeros(24)
    for calls in calls_by_day.values():
        for c in calls:
            s, e = c.q_start_ts, c.q_exit_ts
            if s >= 86_400:  # arrived after 24:00 of its date: outside this timeline
                continue
            h0 = s // 3600
            arr[h0] += 1
            wsum[h0] += e - s
            for h in range(max(s, 0) // 3600, min(e, 86_400 - 1) // 3600 + 1):
                if h > 23:
                    break
                lo, hi = max(s, h * 3600), min(e, (h + 1) * 3600)
                if hi > lo:
                    qsec[h] += hi - lo
    out = {}
    for h in range(24):
        L = qsec[h] / (3600 * n_days)
        lam = arr[h] / (3600 * n_days)
        W = wsum[h] / arr[h] if arr[h] else None
        lw = lam * W if W is not None else None
        out[str(h)] = {
            "time_avg_queue_length": L,
            "arrivals_per_s": lam,
            "mean_wait_s": W,
            "lambda_times_W": lw,
            "ratio_L_over_lambdaW": (L / lw) if lw else None,
        }
    return out
