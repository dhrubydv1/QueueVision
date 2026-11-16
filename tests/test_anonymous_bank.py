"""Parser tests on synthetic rows only (no real call-centre data)."""

from ai_engine.data.anonymous_bank import (
    ParseStats,
    parse_line,
    parse_month,
    validate_records,
    write_tidy,
)

HEADER = (
    "vru+line\tcall_id\tcustomer_id\tpriority\ttype\tdate\tvru_entry\tvru_exit\t"
    "vru_time\tq_start\tq_exit\tq_time\toutcome\tser_start\tser_exit\tser_time\tserver"
)


def row(
    call="1",
    prio="2",
    typ="PS",
    date="990101",
    ve="9:00:00",
    vx="9:00:05",
    vt="5",
    qs="9:00:05",
    qx="9:01:05",
    qt="60",
    out="AGENT",
    ss="9:01:04",
    sx="9:03:04",
    st="120",
    srv="DAN",
    cust="3.06E+08",
    sep="\t",
):
    return sep.join(["AA0101", call, cust, prio, typ, date, ve, vx, vt, qs, qx, qt, out, ss, sx, st, srv])


def write(tmp_path, lines, eol="\r\n"):
    p = tmp_path / "m.txt"
    p.write_bytes((eol.join([HEADER, *lines]) + eol).encode())
    return p


def test_basic_row_and_customer_dropped():
    r = parse_line(row())
    assert r.call_key == "AA0101:1:990101"
    assert r.queued and r.q_start_ts == 32405 and r.q_exit_ts == 32465
    assert r.ser_start_ts == 32464 and r.ser_exit_ts == 32584
    assert not hasattr(r, "customer_id")
    assert "3.06E+08" not in repr(r)


def test_never_queued_and_hang():
    r = parse_line(row(qs="0:00:00", qx="0:00:00", qt="0"))
    assert not r.queued and r.q_start_ts is None
    h = parse_line(row(out="HANG", ss="0:00:00", sx="0:00:00", st="0", srv="NO_SERVER"))
    assert h.ser_start_ts is None and h.outcome == "HANG"


def test_midnight_crossing():
    r = parse_line(
        row(
            ve="23:59:50",
            vx="23:59:55",
            qs="23:59:55",
            qx="0:00:20",
            qt="25",
            ss="0:00:19",
            sx="0:02:00",
            st="101",
        )
    )
    assert r.midnight_crossed
    assert r.q_exit_ts == 86400 + 20 and r.ser_start_ts == 86400 + 19
    assert r.ser_exit_ts == 86400 + 120


def test_space_and_padded_variants(tmp_path):
    spaced = row(sep=" ")
    padded = row(typ=" TT")
    tabbed_with_space = row().replace("\t2\tPS", " 2 PS", 1)  # a tab replaced by a space
    p = write(tmp_path, [row(), spaced, padded, tabbed_with_space, row(out="PHANTOM")])
    st = ParseStats()
    recs = list(parse_month(p, st))
    assert len(recs) == 5 and st.unparseable == 0
    assert recs[2].type == "TT"
    assert st.padded_type_rows == 1 and st.space_for_tab_rows >= 2
    assert st.crlf_rows == 6


def test_unparseable_counted(tmp_path):
    p = write(tmp_path, [row(), "garbage line", row(out="WAT")])
    st = ParseStats()
    assert len(list(parse_month(p, st))) == 1
    assert st.unparseable == 2


def test_validation_counts_and_mismatch(tmp_path):
    lines = [
        row(),
        row(call="2", qt="61"),
        row(call="3", out="PHANTOM"),
        row(call="4", out="HANG", ss="0:00:00", sx="0:00:00", st="0", srv="NO_SERVER"),
    ]
    st = ParseStats()
    recs = list(parse_month(write(tmp_path, lines), st))
    rep = validate_records({"M": (recs, st)})
    assert rep["months"]["M"]["phantom_excluded"] == 1
    assert rep["months"]["M"]["kept"] == 3
    assert rep["q_time_minus_queue_interval"]["plus_1s"] == 1
    assert rep["totals"]["agent_ser_start_before_q_exit"] == 2
    assert abs(rep["hang_share_among_queued"] - 1 / 3) < 1e-9


def test_tidy_has_no_customer_and_no_phantom(tmp_path):
    recs = list(parse_month(write(tmp_path, [row(), row(call="2", out="PHANTOM")])))
    out = tmp_path / "t.csv"
    assert write_tidy(recs, out) == 1
    text = out.read_text()
    assert "customer" not in text and "3.06E+08" not in text


def test_queued_from_exactly_midnight():
    # q_start 0:00:00 but q_time > 0: genuinely queued (protocol v1 rule).
    r = parse_line(
        row(
            ve="0:00:00",
            vx="0:00:00",
            vt="0",
            qs="0:00:00",
            qx="0:00:27",
            qt="27",
            ss="0:00:26",
            sx="0:02:00",
            st="94",
        )
    )
    assert r.queued and r.q_start_ts == 0 and r.q_exit_ts == 27
    never = parse_line(row(qs="0:00:00", qx="0:00:00", qt="0"))
    assert not never.queued
