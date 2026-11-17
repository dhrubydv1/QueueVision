"""Torch-free tests for the pacing experiment variants (measure_realtime, run_pacing_experiments)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import measure_realtime as mr  # noqa: E402
import run_pacing_experiments as rpe  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []
        self.reads = 0

    def now(self) -> float:
        self.reads += 1
        self.t += 0.0001  # each read costs 0.1 ms so a spin loop terminates
        return self.t

    def sleep(self, seconds: float) -> bool:
        self.sleeps.append(seconds)
        self.t += seconds
        return False


def test_spin_tail_sleeps_to_two_ms_before_deadline_then_spins() -> None:
    clock = FakeClock()
    start = clock.t
    wait = mr.make_variant_wait("spin-tail", clock.sleep, clock.now)
    _, waited, warm = wait(0.040)
    assert len(clock.sleeps) == 1
    assert 0.0378 < clock.sleeps[0] < 0.0381  # about 40 ms - 2 ms
    assert clock.t >= start + 0.040  # spun up to the deadline
    assert clock.t < start + 0.0405
    assert waited >= 0.040
    assert warm == 0.0


def test_spin_tail_short_delay_skips_the_sleep() -> None:
    clock = FakeClock()
    wait = mr.make_variant_wait("spin-tail", clock.sleep, clock.now)
    wait(0.001)
    assert clock.sleeps == []


def test_baseline_only_sleeps_and_never_warms() -> None:
    clock = FakeClock()
    calls: list[int] = []
    wait = mr.make_variant_wait("baseline", clock.sleep, clock.now, warmup=lambda: calls.append(1))
    wait(0.04)
    assert clock.sleeps == [0.04]
    assert calls == []


def test_warmup_variant_runs_warmup_once_per_wait_after_the_sleep() -> None:
    clock = FakeClock()
    events: list[str] = []
    orig_sleep = clock.sleep
    wait = mr.make_variant_wait(
        "warmup",
        lambda s: (events.append("sleep"), orig_sleep(s))[1],
        clock.now,
        warmup=lambda: events.append("warm"),
    )
    for _ in range(5):
        _, _, warm = wait(0.03)
        assert warm >= 0.0
    assert events == ["sleep", "warm"] * 5


def test_unknown_variant_rejected() -> None:
    try:
        mr.make_variant_wait("nope", lambda s: None, lambda: 0.0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_cpu_seconds_is_positive_and_monotonic() -> None:
    a = mr.cpu_seconds()
    sum(i * i for i in range(200000))
    assert mr.cpu_seconds() >= a > 0


def test_variant_record_has_cpu_and_warmup_fields() -> None:
    import inspect

    src = inspect.getsource(mr.run_variant)
    for key in (
        '"cpu_s"',
        '"warmup_s"',
        '"warmup_calls"',
        '"update_median_ms"',
        '"waited_s"',
        '"non_wait_s"',
    ):
        assert key in src


def summary_for(update: dict[str, float], cpu: dict[str, float]) -> dict:
    return {
        c: {
            "update_median_ms": {"median": update[c]},
            "cpu_s": {"median": cpu[c]},
        }
        for c in update
    }


def test_decide_applies_rule_and_picks_lowest_cpu() -> None:
    update = {"fast": 33.0, "a": 45.0, "b": 34.0, "c": 36.0, "d": 40.0}
    cpu = {"fast": 9.0, "a": 10.0, "b": 10.5, "c": 10.2, "d": 12.0}
    out = rpe.decide(summary_for(update, cpu))
    assert out["gap_ms"] == 12.0
    assert out["criteria"]["b"]["criterion_1_closes_two_thirds_of_gap"]  # 34 <= 37
    assert out["criteria"]["c"]["criterion_1_closes_two_thirds_of_gap"]  # 36 <= 37
    assert not out["criteria"]["d"]["criterion_1_closes_two_thirds_of_gap"]
    assert out["passing"] == ["b", "c"]
    assert out["decision"] == "c"  # lower CPU


def test_decide_nothing_when_cpu_too_high() -> None:
    update = {"fast": 33.0, "a": 45.0, "b": 45.0, "c": 34.0, "d": 34.0}
    cpu = {"fast": 9.0, "a": 10.0, "b": 10.0, "c": 11.01, "d": 14.0}
    out = rpe.decide(summary_for(update, cpu))
    assert out["decision"] is None


def test_caffeinate_only_wraps_variant_b() -> None:
    for config in rpe.CONFIGS:
        cmd = rpe.command_for(config, Path("v.mp4"), "cam", Path("o"), "t")
        assert (cmd[:2] == ["caffeinate", "-i"]) == (config == "b")
