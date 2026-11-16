"""Parser and validation for the "Anonymous Bank" call-centre logs.

Source: Ilan Guedj and Avi Mandelbaum, Technion SEE Center (documentation
9 Feb 2000). Files are CRLF text, 17 fields per row, nominally tab separated
but a few rows use spaces (and some type values are space padded), so each line
is split on any run of whitespace. ``customer_id`` is dropped at parse time and
is never stored or written anywhere.

Timestamps are seconds since that day's 00:00 and may exceed 86 400 for calls
that cross midnight.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

DAY_S = 86_400
HALF_DAY_S = 43_200
N_FIELDS = 17
OUTCOMES = ("AGENT", "HANG", "PHANTOM")

TIDY_COLUMNS = [
    "call_key",
    "date",
    "type",
    "priority",
    "queued",
    "q_start_ts",
    "q_exit_ts",
    "ser_start_ts",
    "ser_exit_ts",
    "outcome",
    "server",
    "vru_time",
    "q_time",
    "ser_time",
]


@dataclass(frozen=True)
class CallRecord:
    call_key: str  # vru_line:call_id:date
    date: dt.date
    type: str
    priority: int
    queued: bool  # q_start != 0:00:00 or q_time > 0 (protocol v1 rule)
    vru_entry_ts: int
    vru_exit_ts: int
    q_start_ts: int | None
    q_exit_ts: int | None
    ser_start_ts: int | None  # None when service start is 0:00:00
    ser_exit_ts: int | None
    outcome: str
    server: str
    vru_time: int
    q_time: int
    ser_time: int
    midnight_crossed: bool = False


@dataclass
class ParseStats:
    rows: int = 0
    unparseable: int = 0
    wrong_field_count: int = 0
    space_for_tab_rows: int = 0  # tab split gives != 17 fields, whitespace split gives 17
    padded_type_rows: int = 0  # a tab-split field has leading/trailing spaces
    crlf_rows: int = 0
    errors: list[str] = field(default_factory=list)


def parse_hms(text: str) -> int:
    h, m, s = text.split(":")
    hh, mm, ss = int(h), int(m), int(s)
    if not (0 <= mm < 60 and 0 <= ss < 60 and 0 <= hh < 24):
        raise ValueError(f"bad time {text!r}")
    return hh * 3600 + mm * 60 + ss


def _parse_date(text: str) -> dt.date:
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"bad date {text!r}")
    return dt.date(1900 + int(text[:2]), int(text[2:4]), int(text[4:]))


def _after(t: int, ref: int) -> tuple[int, bool]:
    """Place a chained event ``t`` at or after ``ref`` (half-day tolerance).

    Events in one call are at most minutes apart, so a value more than half a
    day before ``ref`` must be past midnight. Small negative jitter (service
    starting 1-2 s before the queue exit) is left alone.
    """
    if t < ref - HALF_DAY_S:
        return t + DAY_S, True
    return t, False


def parse_line(line: str) -> CallRecord:
    f = line.split()
    if len(f) != N_FIELDS:
        raise ValueError(f"expected {N_FIELDS} fields, got {len(f)}")
    # f[2] is customer_id: dropped here.
    vru_line, call_id, _cust, prio, typ, date_s = f[0], f[1], f[2], f[3], f[4], f[5]
    date = _parse_date(date_s)
    outcome = f[12]
    if outcome not in OUTCOMES:
        raise ValueError(f"bad outcome {outcome!r}")
    crossed = False

    vru_entry = parse_hms(f[6])
    vru_exit = parse_hms(f[7])
    if vru_exit < vru_entry:
        vru_exit += DAY_S
        crossed = True

    q_raw_start, q_raw_exit = parse_hms(f[9]), parse_hms(f[10])
    # A call that queued from exactly midnight has q_start 0:00:00 but a positive q_time.
    q_time = int(f[11])
    queued = q_raw_start != 0 or q_time > 0
    q_start = q_exit = None
    if queued:
        q_start, c = _after(q_raw_start, vru_entry)
        crossed |= c
        q_exit = q_raw_exit + (DAY_S if q_start >= DAY_S else 0)
        if q_exit < q_start:
            q_exit += DAY_S
            crossed = True

    s_raw_start, s_raw_exit = parse_hms(f[13]), parse_hms(f[14])
    ser_start = ser_exit = None
    if s_raw_start != 0:
        ref = q_exit if q_exit is not None else vru_exit
        ser_start, c = _after(s_raw_start, ref)
        crossed |= c
        ser_exit = s_raw_exit + (DAY_S if ser_start >= DAY_S else 0)
        if ser_exit < ser_start:
            ser_exit += DAY_S
            crossed = True

    return CallRecord(
        call_key=f"{vru_line}:{call_id}:{date_s}",
        date=date,
        type=typ,
        priority=int(prio),
        queued=queued,
        vru_entry_ts=vru_entry,
        vru_exit_ts=vru_exit,
        q_start_ts=q_start,
        q_exit_ts=q_exit,
        ser_start_ts=ser_start,
        ser_exit_ts=ser_exit,
        outcome=outcome,
        server=f[16],
        vru_time=int(f[8]),
        q_time=q_time,
        ser_time=int(f[15]),
        midnight_crossed=crossed,
    )


def parse_month(path: str | Path, stats: ParseStats | None = None) -> Iterator[CallRecord]:
    """Yield a CallRecord per data row (PHANTOM rows included; filter downstream)."""
    st = stats if stats is not None else ParseStats()
    with open(path, encoding="ascii", errors="replace", newline="") as fh:
        first = True
        for lineno, raw in enumerate(fh, 1):
            if raw.endswith("\r\n"):
                st.crlf_rows += 1
            line = raw.rstrip("\r\n")
            if first:
                first = False
                if line.lower().startswith("vru"):
                    continue
            if not line.strip():
                continue
            st.rows += 1
            tab = line.split("\t")
            if len(tab) != N_FIELDS and len(line.split()) == N_FIELDS:
                st.space_for_tab_rows += 1
            elif len(tab) == N_FIELDS and any(x != x.strip() for x in tab):
                st.padded_type_rows += 1
            try:
                yield parse_line(line)
            except (ValueError, IndexError) as exc:
                st.unparseable += 1
                if len(line.split()) != N_FIELDS:
                    st.wrong_field_count += 1
                if len(st.errors) < 20:
                    st.errors.append(f"line {lineno}: {exc}")


def tidy_row(r: CallRecord) -> list:
    def v(x):
        return "" if x is None else x

    return [
        r.call_key,
        r.date.isoformat(),
        r.type,
        r.priority,
        int(r.queued),
        v(r.q_start_ts),
        v(r.q_exit_ts),
        v(r.ser_start_ts),
        v(r.ser_exit_ts),
        r.outcome,
        r.server,
        r.vru_time,
        r.q_time,
        r.ser_time,
    ]


def write_tidy(records: Iterable[CallRecord], path: str | Path) -> int:
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(TIDY_COLUMNS)
        for r in records:
            if r.outcome == "PHANTOM":
                continue
            w.writerow(tidy_row(r))
            n += 1
    return n


def _diff_dist(diffs: list[int]) -> dict:
    c = Counter("0" if d == 0 else "+1" if d == 1 else "-1" if d == -1 else "other" for d in diffs)
    return {
        "n": len(diffs),
        "zero": c["0"],
        "plus_1s": c["+1"],
        "minus_1s": c["-1"],
        "other": c["other"],
        "min": min(diffs) if diffs else None,
        "max": max(diffs) if diffs else None,
    }


def validate_records(per_month: dict[str, tuple[list[CallRecord], ParseStats]]) -> dict:
    """Aggregate validation counts. Input: month name -> (records, parse stats)."""
    rep: dict = {"months": {}}
    q_diffs: list[int] = []
    s_diffs: list[int] = []
    tot = Counter()
    types, prios = Counter(), Counter()
    hours = Counter()
    by_type_outcome: dict[str, Counter] = {}
    ser_vs_qexit_diffs: list[int] = []
    agent_ser_vs_qexit_diffs: list[int] = []
    for month, (recs, st) in per_month.items():
        kept = [r for r in recs if r.outcome != "PHANTOM"]
        m = {
            "rows": st.rows,
            "parsed": len(recs),
            "unparseable": st.unparseable,
            "phantom_excluded": len(recs) - len(kept),
            "kept": len(kept),
            "days": len({r.date for r in kept}),
            "space_for_tab_rows": st.space_for_tab_rows,
            "padded_type_rows": st.padded_type_rows,
            "crlf_rows": st.crlf_rows,
            "midnight_crossings": sum(r.midnight_crossed for r in kept),
        }
        rep["months"][month] = m
        tot["rows"] += st.rows
        tot["parsed"] += len(recs)
        tot["unparseable"] += st.unparseable
        tot["phantom_excluded"] += m["phantom_excluded"]
        tot["kept"] += len(kept)
        tot["days"] += m["days"]
        tot["space_for_tab_rows"] += st.space_for_tab_rows
        tot["padded_type_rows"] += st.padded_type_rows
        tot["midnight_crossings"] += m["midnight_crossings"]
        for r in kept:
            types[r.type] += 1
            prios[r.priority] += 1
            by_type_outcome.setdefault(r.type, Counter())[r.outcome] += 1
            if r.queued:
                tot["queued"] += 1
                tot[f"queued_{r.outcome}"] += 1
                hours[(r.q_start_ts // 3600) % 24] += 1
                q_diffs.append(r.q_time - (r.q_exit_ts - r.q_start_ts))
            if r.ser_start_ts is not None:
                s_diffs.append(r.ser_time - (r.ser_exit_ts - r.ser_start_ts))
            if r.outcome == "HANG" and (r.ser_start_ts is not None or r.ser_time != 0):
                tot["hang_with_service_times"] += 1
            if r.outcome == "AGENT" and r.queued and r.ser_start_ts is None:
                tot["agent_queued_ser_start_zero"] += 1
            if r.outcome == "AGENT" and r.ser_start_ts is None:
                tot["agent_ser_start_zero"] += 1
            if r.queued and r.ser_start_ts is not None:
                d = r.ser_start_ts - r.q_exit_ts
                ser_vs_qexit_diffs.append(d)
                if r.outcome == "AGENT":
                    agent_ser_vs_qexit_diffs.append(d)
                    tot["agent_queued_served"] += 1
                    if d < 0:
                        tot["agent_ser_start_before_q_exit"] += 1
                    elif d == 0:
                        tot["agent_ser_start_equals_q_exit"] += 1
                    else:
                        tot["agent_ser_start_after_q_exit"] += 1
                if d < 0:
                    tot["ser_start_before_q_exit"] += 1
                elif d != 0:
                    tot["ser_start_differs_from_q_exit_later"] += 1
    rep["totals"] = {k: v for k, v in sorted(tot.items())}
    q = tot["queued"]
    rep["hang_share_among_queued"] = (tot["queued_HANG"] / q) if q else None
    rep["q_time_minus_queue_interval"] = _diff_dist(q_diffs)
    rep["ser_time_minus_service_interval"] = _diff_dist(s_diffs)
    rep["ser_start_minus_q_exit_all_served"] = _diff_dist(ser_vs_qexit_diffs)
    rep["ser_start_minus_q_exit_agent"] = _diff_dist(agent_ser_vs_qexit_diffs)
    rep["calls_per_type"] = dict(sorted(types.items()))
    rep["calls_per_priority"] = {str(k): v for k, v in sorted(prios.items())}
    rep["outcomes_per_type"] = {t: dict(sorted(c.items())) for t, c in sorted(by_type_outcome.items())}
    rep["queued_calls_per_hour"] = {str(h): hours.get(h, 0) for h in range(24)}
    return rep
