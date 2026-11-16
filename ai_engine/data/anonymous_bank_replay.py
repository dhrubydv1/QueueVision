"""Replay Anonymous Bank call logs through QueueVision's own wait predictor.

Implements ``docs/anonymous_bank_protocol.md`` (version 1). The adapter only calls the
engine's public functions (``ServiceRateEstimator`` / ``predict_wait``); nothing is copied.

Rules (from the protocol):

* Each date is replayed alone from 0 s; one estimator per day per queue definition.
* Calls are ordered by (q_start, vru_entry, call_key). Queue length at t = q_start(c) is the
  number of other calls that are earlier in that order and whose q_exit is strictly after t.
* Service events are ``ser_start`` of AGENT calls (queued, in this queue), strictly before t.
  HANG calls are never service events.
* ``estimate`` is called once at every distinct event time (q_start, q_exit, ser_start) in
  time order. At a time T the service events strictly before T have been added, the estimate
  is taken, predictions for calls starting at T use it, and only then do services at T count
  (for later times). Event-driven sampling differs from the video engine's per-frame calls.
"""

from __future__ import annotations

import heapq
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass

from ai_engine.data.anonymous_bank import CallRecord
from ai_engine.queue.camera_config import ServiceRateSettings
from ai_engine.queue.waiting_time import ServiceRateEstimator, predict_wait

PRIMARY_TYPES = frozenset({"PS", "PE", "NE", "NW", "TT"})
PS_PE_TYPES = frozenset({"PS", "PE"})
QUEUE_DEFINITIONS: dict[str, frozenset[str] | None] = {
    "primary": PRIMARY_TYPES,
    "all_types": None,
    "ps_pe": PS_PE_TYPES,
}
RECENT_N = 20
RECENT_MIN = 5


@dataclass(frozen=True)
class Prediction:
    call_key: str
    date: object
    type: str
    priority: int
    outcome: str
    q_start: int
    q_exit: int
    wait: int  # q_exit - q_start
    queue_length: int
    pred: float | None  # predicted wait (None unless status ok)
    low: float | None
    high: float | None
    les: float | None
    recent_mean: float | None


def select_queue(records: Iterable[CallRecord], definition: str = "primary") -> list[CallRecord]:
    """Queued AGENT/HANG calls of the queue definition (PHANTOM, never-queued excluded)."""
    types = QUEUE_DEFINITIONS[definition]
    return [
        r
        for r in records
        if r.queued and r.outcome in ("AGENT", "HANG") and (types is None or r.type in types)
    ]


def group_by_day(calls: Iterable[CallRecord]) -> dict[object, list[CallRecord]]:
    days: dict[object, list[CallRecord]] = defaultdict(list)
    for c in calls:
        days[c.date].append(c)
    return dict(sorted(days.items()))


def order_key(c: CallRecord) -> tuple[int, int, str]:
    return (c.q_start_ts, c.vru_entry_ts, c.call_key)


def queue_lengths(ordered: list[CallRecord]) -> list[int]:
    """Exact queue length at each call's q_start, for calls already in protocol order."""
    heap: list[int] = []
    out = []
    for c in ordered:
        t = c.q_start_ts
        while heap and heap[0] <= t:  # q_exit must be strictly after t
            heapq.heappop(heap)
        out.append(len(heap))
        heapq.heappush(heap, c.q_exit_ts)
    return out


def replay_day(
    calls: list[CallRecord],
    settings: ServiceRateSettings | None = None,
    service_starts: list[int] | None = None,
) -> list[Prediction]:
    """Replay one date of one queue; returns a Prediction per call (HANG included).

    ``service_starts`` overrides the service events (post-hoc sensitivity S1 passes the
    ``ser_start`` of every AGENT call of the queue's types, queued or not). Default (protocol
    v1): ``ser_start`` of the queue's own queued AGENT calls.

    Deliberate difference from ``QueueAnalyzer.update``: it adds the service starts of a frame
    *before* calling ``estimate`` for that frame; here only services strictly before t count.
    """
    if not calls:
        return []
    settings = settings or ServiceRateSettings()
    ordered = sorted(calls, key=order_key)
    qlens = queue_lengths(ordered)
    if service_starts is None:
        service_starts = [
            c.ser_start_ts for c in ordered if c.outcome == "AGENT" and c.ser_start_ts is not None
        ]
    services = sorted(service_starts)
    # HANG ser_start is an estimate() time (as the protocol lists) but never a service event.
    hang_service_times = {
        c.ser_start_ts for c in ordered if c.outcome == "HANG" and c.ser_start_ts is not None
    }
    agent_exits = sorted(
        ((c.q_exit_ts, order_key(c), c.q_exit_ts - c.q_start_ts) for c in ordered if c.outcome == "AGENT"),
    )
    times = {c.q_start_ts for c in ordered} | {c.q_exit_ts for c in ordered}
    times |= set(services) | hang_service_times
    by_start: dict[int, list[int]] = defaultdict(list)
    for i, c in enumerate(ordered):
        by_start[c.q_start_ts].append(i)

    estimator = ServiceRateEstimator(settings, start_s=0.0)
    si = 0
    ai = 0
    recent: deque[int] = deque(maxlen=RECENT_N)
    out: list[Prediction] = []
    for T in sorted(times):
        while si < len(services) and services[si] < T:
            estimator.add_service(float(services[si]))
            si += 1
        while ai < len(agent_exits) and agent_exits[ai][0] < T:
            recent.append(agent_exits[ai][2])
            ai += 1
        rate = estimator.estimate(float(T))
        for i in by_start.get(T, ()):
            c = ordered[i]
            p = predict_wait(qlens[i], rate)
            ok = p.status == "ok"
            out.append(
                Prediction(
                    call_key=c.call_key,
                    date=c.date,
                    type=c.type,
                    priority=c.priority,
                    outcome=c.outcome,
                    q_start=c.q_start_ts,
                    q_exit=c.q_exit_ts,
                    wait=c.q_exit_ts - c.q_start_ts,
                    queue_length=qlens[i],
                    pred=p.predicted_wait_s if ok else None,
                    low=p.low_s if ok else None,
                    high=p.high_s if ok else None,
                    les=float(recent[-1]) if recent else None,
                    recent_mean=sum(recent) / len(recent) if len(recent) >= RECENT_MIN else None,
                )
            )
    return out


def replay_queue(
    calls: Iterable[CallRecord],
    settings: ServiceRateSettings | None = None,
    all_service_starts: dict[object, list[int]] | None = None,
) -> list[Prediction]:
    out: list[Prediction] = []
    for date, day in group_by_day(calls).items():
        starts = None if all_service_starts is None else all_service_starts.get(date, [])
        out.extend(replay_day(day, settings, starts))
    return out


def all_service_starts_by_day(
    records: Iterable[CallRecord], definition: str = "primary"
) -> dict[object, list[int]]:
    """Post-hoc S1: ser_start of every AGENT call of the queue's types, queued or not."""
    types = QUEUE_DEFINITIONS[definition]
    out: dict[object, list[int]] = defaultdict(list)
    for r in records:
        if r.outcome == "AGENT" and r.ser_start_ts is not None and (types is None or r.type in types):
            out[r.date].append(r.ser_start_ts)
    return dict(out)


def regression_fit(preds: Iterable[Prediction]) -> tuple[float, float]:
    """OLS wait = a + b * (queue_length + 1) on AGENT calls. Returns (a, b)."""
    xs, ys = [], []
    for p in preds:
        if p.outcome == "AGENT":
            xs.append(p.queue_length + 1.0)
            ys.append(float(p.wait))
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    return my - b * mx, b


def carried_over_count(calls: Iterable[CallRecord]) -> int:
    """Calls still queued at/after 24:00 of their date (not carried into the next replay)."""
    return sum(1 for c in calls if c.q_exit_ts >= 86_400)
