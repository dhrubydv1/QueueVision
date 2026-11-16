"""Replay adapter tests on synthetic calls only."""

import datetime as dt
import random

from ai_engine.data.anonymous_bank import CallRecord
from ai_engine.data.anonymous_bank_replay import (
    queue_lengths,
    regression_fit,
    replay_day,
    select_queue,
)
from ai_engine.queue.camera_config import ServiceRateSettings
from ai_engine.queue.waiting_time import ServiceRateEstimator, predict_wait

D = dt.date(1999, 1, 4)


def call(key, qs, qx, ss=None, out="AGENT", typ="PS", ve=None, prio=2, queued=True):
    return CallRecord(
        call_key=key,
        date=D,
        type=typ,
        priority=prio,
        queued=queued,
        vru_entry_ts=qs - 5 if ve is None else ve,
        vru_exit_ts=qs,
        q_start_ts=qs,
        q_exit_ts=qx,
        ser_start_ts=ss,
        ser_exit_ts=None if ss is None else ss + 100,
        outcome=out,
        server="X",
        vru_time=5,
        q_time=qx - qs,
        ser_time=100,
    )


def by_key(preds):
    return {p.call_key: p for p in preds}


def synthetic():
    cs = []
    for i in range(12):  # steady service every 40 s
        cs.append(call(f"a{i}", 20 + 30 * i, 100 + 40 * i, ss=99 + 40 * i))
    cs.append(call("h1", 150, 400, ss=140, out="HANG"))  # HANG with a service time
    return cs


def test_adapter_self_consistent_with_direct_engine_calls():
    """Self-consistency of the adapter against direct calls of the engine's functions.

    This does NOT run ``QueueAnalyzer`` (it needs detections and a camera config). It mirrors the
    analyzer's estimator usage (``add_service`` then ``estimate`` then ``predict_wait``) with
    the one deliberate difference tested in ``test_deliberate_difference_from_queue_analyzer``:
    the analyzer adds same-frame service starts before ``estimate``; the protocol counts only
    services strictly before t.
    """
    cs = synthetic()
    preds = by_key(replay_day(cs))
    services = sorted(c.ser_start_ts for c in cs if c.outcome == "AGENT")
    times = sorted(
        {c.q_start_ts for c in cs}
        | {c.q_exit_ts for c in cs}
        | set(services)
        | {c.ser_start_ts for c in cs if c.outcome == "HANG"}
    )
    ordered = sorted(cs, key=lambda c: (c.q_start_ts, c.vru_entry_ts, c.call_key))
    est = ServiceRateEstimator(ServiceRateSettings(), start_s=0.0)
    added = 0
    n_ok = 0
    for T in times:
        while added < len(services) and services[added] < T:  # strictly before T
            est.add_service(float(services[added]))
            added += 1
        rate = est.estimate(float(T))
        for c in ordered:
            if c.q_start_ts != T:
                continue
            ck = (c.q_start_ts, c.vru_entry_ts, c.call_key)
            ql = sum(
                1
                for o in ordered
                if o is not c and o.q_exit_ts > T and (o.q_start_ts, o.vru_entry_ts, o.call_key) < ck
            )
            exp = predict_wait(ql, rate)
            got = preds[c.call_key]
            assert got.queue_length == ql
            assert got.pred == exp.predicted_wait_s
            assert got.low == exp.low_s and got.high == exp.high_s
            n_ok += exp.status == "ok"
    assert n_ok > 5


def test_deliberate_difference_from_queue_analyzer():
    # QueueAnalyzer-style: service start at T is added BEFORE estimate(T), so it counts at T.
    # Protocol: a service at exactly t does not count for the prediction at t.
    starts = [30, 50, 100]
    probe = call("p", 100, 400)
    cs = [call(f"s{i}", 1 + i, 500 + i, ss=s) for i, s in enumerate(starts)]
    got = by_key(replay_day([*cs, probe]))["p"]
    analyzer_style = ServiceRateEstimator(ServiceRateSettings(), 0.0)
    for s in starts:
        analyzer_style.add_service(float(s))
    protocol_style = ServiceRateEstimator(ServiceRateSettings(), 0.0)
    for s in starts[:2]:
        protocol_style.add_service(float(s))
    ql = got.queue_length
    assert got.pred == predict_wait(ql, protocol_style.estimate(100.0)).predicted_wait_s
    assert got.pred != predict_wait(ql, analyzer_style.estimate(100.0)).predicted_wait_s


def test_hang_service_time_is_an_estimate_time_but_never_a_service():
    base = [call(f"s{i}", 10 * i + 1, 400 + i, ss=20 + 10 * i) for i in range(6)]
    probe = call("p", 200, 900)
    plain = by_key(replay_day([*base, probe]))["p"]
    with_hang = by_key(replay_day([*base, probe, call("h", 150, 800, ss=190, out="HANG")]))["p"]
    # same rate (pred / queue length), so no service was added by the HANG call
    assert abs(with_hang.pred / with_hang.queue_length - plain.pred / plain.queue_length) < 1e-9
    assert with_hang.queue_length == plain.queue_length + 1  # but it is in the queue


def test_all_service_starts_s1_counts_unqueued_agents():
    from ai_engine.data.anonymous_bank_replay import all_service_starts_by_day

    queued = [call(f"s{i}", 10 * i + 1, 400, ss=20 + 20 * i) for i in range(3)]
    unq = [call(f"u{i}", 0, 0, ss=25 + 20 * i, queued=False) for i in range(3)]
    other = call("x", 0, 0, ss=30, typ="IN", queued=False)
    probe = call("p", 100, 900)
    starts = all_service_starts_by_day([*queued, *unq, other, probe], "primary")
    assert sorted(starts[D]) == [20, 25, 40, 45, 60, 65]  # IN excluded, probe has no ser_start
    v1 = by_key(replay_day([*queued, probe]))["p"]
    s1 = by_key(replay_day([*queued, probe], service_starts=starts[D]))["p"]
    assert v1.pred != s1.pred
    est = ServiceRateEstimator(ServiceRateSettings(), 0.0)
    for s in starts[D]:
        est.add_service(float(s))
    # estimate history at earlier events is irrelevant for the point rate
    assert s1.pred == predict_wait(s1.queue_length, est.estimate(100.0)).predicted_wait_s


def test_no_look_ahead_append_and_shuffle():
    cs = synthetic()
    base = by_key(replay_day(cs))
    t = 200
    extra = [call(f"z{i}", t + 1 + i, t + 50 + i, ss=t + 2 + i) for i in range(10)]
    later = [c for c in cs if c.q_start_ts > t]
    withx = by_key(replay_day(cs + extra))
    shuffled = list(cs)
    random.Random(1).shuffle(shuffled)
    shuf = by_key(replay_day(shuffled))
    for k, p in base.items():
        assert shuf[k] == p
        if p.q_start <= t:
            q = withx[k]
            assert (q.pred, q.low, q.high, q.queue_length, q.les, q.recent_mean) == (
                p.pred,
                p.low,
                p.high,
                p.queue_length,
                p.les,
                p.recent_mean,
            )
    # changing the future of the early calls (exits/services after t) is also harmless
    mod = [
        c if c.q_start_ts <= t else call(c.call_key, c.q_start_ts, c.q_exit_ts + 999, ss=c.q_exit_ts + 999)
        for c in cs
    ]
    m = by_key(replay_day(mod))
    for c in cs:
        if c.q_start_ts <= t:
            assert m[c.call_key].pred == base[c.call_key].pred
    assert later


def test_tie_order_and_strict_rules():
    a = call("a", 100, 200, ve=90)
    b = call("b", 100, 300, ve=95)  # same q_start, later vru_entry: sees a, a does not see b
    c = call("c", 100, 300, ve=95)  # same vru_entry: ordered by key
    d = call("d", 200, 400)  # a exits exactly at 200: not counted
    p = by_key(replay_day([b, a, d, c]))
    assert [p[k].queue_length for k in "abcd"] == [0, 1, 2, 2]
    assert queue_lengths([a, b, c, d]) == [0, 1, 2, 2]


def test_service_event_strictly_before_t_and_hang_excluded():
    cs = [call(f"s{i}", 10 * i + 1, 30 * i + 40, ss=100 + 10 * i) for i in range(6)]
    probe = call("p", 120, 500)  # services at 100,110 are < 120; 120 itself must not count
    cs.append(call("s_at_120", 5, 600, ss=120))
    h = call("h", 121, 700, ss=122, out="HANG")
    p0 = by_key(replay_day([*cs, probe, h]))["p"]
    ref = ServiceRateEstimator(ServiceRateSettings(), 0.0)
    for s in (100, 110):
        ref.add_service(float(s))
    # services strictly before 120: 100, 110 (and none at/after); 120 and HANG excluded
    times = sorted(
        {c.q_start_ts for c in [*cs, probe, h]}
        | {c.q_exit_ts for c in [*cs, probe, h]}
        | {c.ser_start_ts for c in cs if c.ser_start_ts}
    )
    est = ServiceRateEstimator(ServiceRateSettings(), 0.0)
    allsv = sorted(c.ser_start_ts for c in cs if c.ser_start_ts)
    i = 0
    rate = None
    for T in times:
        while i < len(allsv) and allsv[i] < T:
            est.add_service(float(allsv[i]))
            i += 1
        rate = est.estimate(float(T))
        if T == 120:
            break
    assert p0.pred == predict_wait(p0.queue_length, rate).predicted_wait_s


def test_baselines_and_population():
    cs = [call(f"w{i}", 10 + 20 * i, 60 + 20 * i + i, ss=59 + 20 * i) for i in range(30)]
    cs.append(call("late", 1000, 1100, ss=1100))
    p = by_key(replay_day(cs))["late"]
    assert p.les == 60 + 20 * 29 + 29 - (10 + 20 * 29)  # last AGENT wait (q_exit < 1000)
    waits = [(60 + 20 * i + i) - (10 + 20 * i) for i in range(30)]
    assert abs(p.recent_mean - sum(waits[-20:]) / 20) < 1e-9
    early = by_key(replay_day(cs))["w0"]
    assert early.les is None and early.recent_mean is None
    recs = [
        call("x", 1, 2, typ="IN"),
        call("y", 1, 2, typ="PS"),
        call("z", 1, 2, out="PHANTOM"),
        call("n", 1, 2, queued=False),
    ]
    assert [r.call_key for r in select_queue(recs, "primary")] == ["y"]
    assert [r.call_key for r in select_queue(recs, "all_types")] == ["x", "y"]


def test_regression_fit_known():
    from ai_engine.data.anonymous_bank_replay import Prediction

    def pr(ql, w):
        return Prediction("k", D, "PS", 2, "AGENT", 0, w, w, ql, None, None, None, None, None)

    a, b = regression_fit([pr(0, 40), pr(1, 70), pr(3, 130), pr(2, 99999)][:3])
    assert abs(b - 30) < 1e-9 and abs(a - 10) < 1e-9
