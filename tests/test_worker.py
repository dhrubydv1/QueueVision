"""The live worker with a fake source, fake tracker, fake clock and a recording HTTP opener."""

from __future__ import annotations

import io
import itertools
import json
import logging
import signal
import threading
import urllib.error

import numpy as np
import pytest

from tests.conftest import FakeTracker
from tests.test_live_loop import FakeClock, ReadySource, SilentSource
from worker import ctl
from worker.client import IngestClient
from worker.session import main, redact

ARGS = ["--video-id", "7", "--camera", "cad_seq25", "--api", "http://127.0.0.1:8000", "--output-dir"]
ENV = {"QUEUEVISION_WORKER_TOKEN": "tok123"}


class Recorder:
    """Fake ``urlopen``: records every request and answers 204 (or raises)."""

    def __init__(self, clock: FakeClock, error: Exception | None = None) -> None:
        self.clock, self.error = clock, error
        self.requests: list[dict] = []
        self.attempts: list[str] = []  # every request tried, including ones that failed

    def __call__(self, request, timeout=None):
        self.attempts.append(request.full_url.rsplit("/", 1)[1])
        if self.error is not None:
            raise self.error
        body = request.data
        self.requests.append(
            {
                "route": request.full_url.rsplit("/", 1)[1],
                "url": request.full_url,
                "headers": {k.lower(): v for k, v in request.header_items()},
                "raw": body,
                "json": json.loads(body)
                if request.get_header("Content-type") == "application/json"
                else None,
                "time": self.clock.now,
            }
        )

        class Response:
            status = 204

            def close(self) -> None:
                pass

        return Response()

    def routes(self, name: str) -> list[dict]:
        return [r for r in self.requests if r["route"] == name]


class StoppingSource(ReadySource):
    """Sets the stop flag once the fake clock reaches ``stop_at``."""

    def __init__(self, clock, stop: threading.Event, stop_at: float) -> None:
        super().__init__(clock)
        self.stop, self.stop_at = stop, stop_at

    def read_latest(self, timeout_s: float):
        if self.clock.now >= self.stop_at:
            self.stop.set()
        return super().read_latest(timeout_s)


def run_main(tmp_path, clock, recorder, source, stop=None, tracker=FakeTracker, **extra):
    return main(
        [*ARGS, str(tmp_path)],
        ENV,
        tracker_factory=tracker,
        source_opener=lambda live, env: source,
        opener=recorder,
        clock=clock,
        sleep=clock.sleep,
        stop=stop or threading.Event(),
        **extra,
    )


def test_batches_once_per_second_with_progress_and_rows(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    stop = threading.Event()
    code = run_main(
        tmp_path, clock, rec, StoppingSource(clock, stop, 3.5), stop, wall=lambda: 1000.0 + clock.now
    )
    assert code == 0
    batches = rec.routes("batch")
    assert 3 <= len(batches) <= 5
    assert all(r["headers"]["x-worker-token"] == "tok123" for r in rec.requests)
    gaps = [b["time"] - a["time"] for a, b in itertools.pairwise(batches)]
    assert all(0.95 <= g <= 1.3 for g in gaps[:-1])
    first = batches[0]["json"]
    assert first["progress"]["video_id"] == 7 and first["progress"]["type"] == "progress"
    assert first["frames_processed"] >= 1
    # capture time is the fake clock at the send, so wall - (now - capture) == wall at the send
    assert first["captured_at_unix"] == pytest.approx(1000.0 + batches[0]["time"], abs=1e-6)
    seconds = [int(row["time_s"]) for b in batches for row in b["json"]["timeseries"]]
    assert seconds == sorted(set(seconds)) and len(seconds) >= 3  # one row per video second


def test_preview_rate_limited(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    stop = threading.Event()
    run_main(tmp_path, clock, rec, StoppingSource(clock, stop, 3.0), stop)
    previews = rec.routes("preview")
    assert 10 <= len(previews) <= 16  # 30 frames in 3 s, at most one per 0.2 s
    times = [p["time"] for p in previews]
    assert all(b - a >= 0.2 - 1e-9 for a, b in itertools.pairwise(times))
    assert all(p["raw"][:2] == b"\xff\xd8" and len(p["raw"]) <= 2 * 1024 * 1024 for p in previews)
    assert previews[0]["headers"]["content-type"] == "image/jpeg"


def test_heartbeat_when_idle(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    code = run_main(tmp_path, clock, rec, SilentSource(clock))
    assert code == 1  # no frame ever arrived
    beats = rec.routes("heartbeat")
    assert len(beats) >= 4
    assert set(beats[-1]["json"]) == {"frames_processed", "processing_fps"}
    gaps = [b["time"] - a["time"] for a, b in itertools.pairwise(beats[1:])]
    assert all(g >= 2.0 - 1e-9 for g in gaps)


def test_exits_when_api_unreachable(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock, error=urllib.error.URLError("refused"))
    code = run_main(tmp_path, clock, rec, ReadySource(clock))
    assert code == 3
    assert rec.requests == []  # nothing was accepted
    assert rec.attempts and "final" not in rec.attempts  # and no final was even tried
    assert 15.0 < clock.now < 16.5


def test_unreachable_mid_run_sends_no_final(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    source = ReadySource(clock)
    original = source.read_latest

    def read_latest(timeout_s):
        if clock.now >= 2.0:
            rec.error = urllib.error.URLError("down")
        return original(timeout_s)

    source.read_latest = read_latest
    assert run_main(tmp_path, clock, rec, source) == 3
    assert "final" not in rec.attempts
    assert 17.0 <= clock.now < 19.0


def test_sigterm_sends_completed_final(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    stop = threading.Event()
    assert run_main(tmp_path, clock, rec, StoppingSource(clock, stop, 1.5), stop) == 0
    last = rec.requests[-1]
    assert last["route"] == "final"
    assert last["json"]["status"] == "completed" and last["json"]["error"] is None
    assert last["json"]["report"]["stopped_by"] == "operator"
    assert len(rec.routes("final")) == 1


def test_leftover_rows_flushed_before_final(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    stop = threading.Event()
    assert run_main(tmp_path, clock, rec, StoppingSource(clock, stop, 0.5), stop) == 0
    routes = [r["route"] for r in rec.requests if r["route"] in ("batch", "final")]
    assert routes == ["batch", "final"]  # no timed batch yet: this one is the flush
    batch = rec.routes("batch")[0]["json"]
    assert len(batch["timeseries"]) == 1 and batch["frames_processed"] >= 1


def test_sigterm_during_source_open_completes_without_report(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    stop = threading.Event()
    source = ReadySource(clock)
    closed: list[int] = []
    source.close = lambda: closed.append(1)
    ran: list[int] = []

    def opener(live, env):
        stop.set()  # SIGTERM while the source is opening
        return source

    code = main(
        [*ARGS, str(tmp_path)],
        ENV,
        tracker_factory=FakeTracker,
        source_opener=opener,
        run_live_fn=lambda *a, **k: ran.append(1),
        opener=rec,
        clock=clock,
        sleep=clock.sleep,
        stop=stop,
    )
    assert code == 0 and ran == [] and closed == [1]
    assert rec.requests[-1]["json"] == {"status": "completed", "error": None, "report": None}


def test_sigterm_during_startup_completes_without_report(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    stop = threading.Event()

    def tracker_factory():
        stop.set()  # SIGTERM arrives while the model is loading
        return FakeTracker()

    opened: list[int] = []
    code = main(
        [*ARGS, str(tmp_path)],
        ENV,
        tracker_factory=tracker_factory,
        source_opener=lambda live, env: opened.append(1),
        opener=rec,
        clock=clock,
        sleep=clock.sleep,
        stop=stop,
    )
    assert code == 0 and opened == []
    assert rec.requests[-1]["json"] == {"status": "completed", "error": None, "report": None}


def test_sigterm_handler_installed_and_restored(tmp_path):
    clock = FakeClock()
    seen = {}
    before = signal.getsignal(signal.SIGTERM)

    def fake_run_live(*args, **kwargs):
        seen["handler"] = signal.getsignal(signal.SIGTERM)
        return {"stopped_by": "operator"}

    code = run_main(tmp_path, clock, Recorder(clock), ReadySource(clock), run_live_fn=fake_run_live)
    assert code == 0
    assert callable(seen["handler"]) and seen["handler"] is not before
    assert signal.getsignal(signal.SIGTERM) is before


def test_source_error_sends_failed_reason(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    assert run_main(tmp_path, clock, rec, SilentSource(clock)) == 1
    final = rec.requests[-1]
    assert final["route"] == "final"
    assert final["json"] == {"status": "failed", "error": "camera_unavailable", "report": None}


def test_engine_error_sends_failed_final_without_url(tmp_path, caplog):
    class Boom(FakeTracker):
        def update(self, frame: np.ndarray):
            raise RuntimeError("cannot read rtsp://user:secret@h/x now")

    clock = FakeClock()
    rec = Recorder(clock)
    with caplog.at_level(logging.DEBUG):
        code = run_main(tmp_path, clock, rec, ReadySource(clock), tracker=Boom)
    assert code == 1
    assert rec.requests[-1]["json"] == {"status": "failed", "error": "engine_error", "report": None}
    assert all(b"secret" not in r["raw"] for r in rec.requests)
    assert "secret" not in caplog.text
    assert "rtsp://[redacted]" in caplog.text


def test_redact():
    assert redact("a rtsp://u:p@h:554/s?x=1 b") == "a rtsp://[redacted] b"
    assert redact("no url") == "no url"


def test_token_from_env_only(tmp_path, capsys):
    assert main([*ARGS, str(tmp_path)], {}) == 2
    with pytest.raises(SystemExit):
        main(["--help"], ENV)
    assert "--token" not in capsys.readouterr().out


def test_client_never_raises_and_tracks_last_ok():
    clock = FakeClock()
    client = IngestClient("http://x", 1, "t", opener=Recorder(clock, error=OSError("no")), clock=clock)
    clock.now = 5.0
    assert client.final("completed", None, None) is False
    assert client.seconds_since_ok() == 5.0
    client._opener = Recorder(clock)
    assert client.heartbeat(1, 2.0) is True
    assert client.seconds_since_ok() == 0.0


class FakeApi:
    def __init__(self, answers: dict[tuple[str, str], object]) -> None:
        self.answers, self.calls = answers, []

    def __call__(self, request, timeout=None):
        key = (request.get_method(), request.full_url.split("8000", 1)[1])
        self.calls.append(key)
        if key not in self.answers:
            raise urllib.error.HTTPError(
                request.full_url, 409, "Conflict", {}, io.BytesIO(b'{"detail": "busy"}')
            )
        body = json.dumps(self.answers[key]).encode()

        class Response(io.BytesIO):
            pass

        return Response(body)


def test_ctl_start_prints_live_url():
    api = FakeApi({("POST", "/api/cameras/cad_seq25/live"): {"video_id": 12, "kind": "live"}})
    out = io.StringIO()
    assert ctl.main(["start", "cad_seq25"], opener=api, out=out) == 0
    assert "video id 12" in out.getvalue()
    assert "http://127.0.0.1:8000/live?id=12" in out.getvalue()


def test_ctl_stop_status_and_error(capsys):
    live = [
        {
            "video_id": 12,
            "camera_id": "cad_seq25",
            "source_kind": "clip",
            "heartbeat_age_s": 0.4,
            "processing_fps": 9.8,
        }
    ]
    api = FakeApi({("GET", "/api/live"): live, ("POST", "/api/videos/12/stop"): None})
    out = io.StringIO()
    assert ctl.main(["status", "cad_seq25"], opener=api, out=out) == 0
    assert "video id 12" in out.getvalue()
    assert ctl.main(["stop", "cad_seq25"], opener=api, out=out) == 0
    assert ("POST", "/api/videos/12/stop") in api.calls
    assert ctl.main(["start", "cad_seq25"], opener=FakeApi({})) == 1
    assert "409" in capsys.readouterr().err


def test_ctl_contract_mismatch_prints_error_not_traceback(capsys):
    api = FakeApi({("POST", "/api/cameras/cad_seq25/live"): {"id": 12}})
    assert ctl.main(["start", "cad_seq25"], opener=api) == 1
    assert capsys.readouterr().err.startswith("error:")
    garbled = FakeApi({("GET", "/api/live"): None})
    garbled.answers[("GET", "/api/live")] = "not json"
    garbled_call = lambda request, timeout=None: io.BytesIO(b"<html>")  # noqa: E731
    assert ctl.main(["status"], opener=garbled_call) == 1
    assert "error:" in capsys.readouterr().err


def test_batch_seq_increments_and_retry_keeps_it(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    failures = {"left": 1}
    failed_seqs: list[int] = []

    def flaky(request, timeout=None):
        if request.full_url.endswith("/batch") and failures["left"]:
            failures["left"] -= 1
            failed_seqs.append(json.loads(request.data)["seq"])
            rec.attempts.append("batch")
            raise urllib.error.URLError("blip")
        return rec(request, timeout)

    stop = threading.Event()
    assert run_main(tmp_path, clock, rec, StoppingSource(clock, stop, 3.5), stop, wall=lambda: 1.0) == 0
    # sanity: the clean run numbers its batches 1, 2, 3, ...
    seqs = [b["json"]["seq"] for b in rec.routes("batch")]
    assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) >= 3

    clock = FakeClock()
    rec = Recorder(clock)
    stop = threading.Event()
    code = main(
        [*ARGS, str(tmp_path)],
        ENV,
        tracker_factory=FakeTracker,
        source_opener=lambda live, env: StoppingSource(clock, stop, 3.5),
        opener=flaky,
        clock=clock,
        sleep=clock.sleep,
        stop=stop,
    )
    assert code == 0
    accepted = [b["json"] for b in rec.routes("batch")]
    assert [b["seq"] for b in accepted] == list(range(1, len(accepted) + 1))
    assert failed_seqs == [1] and accepted[0]["seq"] == 1  # the retry reuses the failed batch's seq
    assert rec.attempts.count("batch") == len(accepted) + 1  # one failed attempt, resent under seq 1
    assert accepted[0]["progress"] is not None  # the retried batch is the original one, not a rebuilt one


def test_heartbeats_continue_while_batches_fail(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)

    def batches_fail(request, timeout=None):
        if request.full_url.endswith("/batch"):
            rec.attempts.append("batch")
            raise urllib.error.URLError("blip")
        return rec(request, timeout)

    stop = threading.Event()
    main(
        [*ARGS, str(tmp_path)],
        ENV,
        tracker_factory=FakeTracker,
        source_opener=lambda live, env: StoppingSource(clock, stop, 9.0),
        opener=batches_fail,
        clock=clock,
        sleep=clock.sleep,
        stop=stop,
    )
    beats = [b["time"] for b in rec.routes("heartbeat")]
    during = [t for t in beats if 0.5 < t < 9.0]
    assert len(during) >= 3  # roughly every 2 s although every batch attempt fails
    assert all(b - a <= 2.5 for a, b in itertools.pairwise(during))


def test_final_is_retried_up_to_three_attempts(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)
    failures = {"left": 2}

    def flaky_final(request, timeout=None):
        if request.full_url.endswith("/final") and failures["left"]:
            failures["left"] -= 1
            rec.attempts.append("final")
            raise urllib.error.URLError("blip")
        return rec(request, timeout)

    stop = threading.Event()
    code = main(
        [*ARGS, str(tmp_path)],
        ENV,
        tracker_factory=FakeTracker,
        source_opener=lambda live, env: StoppingSource(clock, stop, 2.0),
        opener=flaky_final,
        clock=clock,
        sleep=clock.sleep,
        stop=stop,
    )
    assert code == 0
    assert rec.attempts.count("final") == 3  # two failures, then accepted
    (final,) = rec.routes("final")
    assert final["json"]["status"] == "completed"


def test_final_gives_up_after_three_attempts(tmp_path):
    clock = FakeClock()
    rec = Recorder(clock)

    def never(request, timeout=None):
        if request.full_url.endswith("/final"):
            rec.attempts.append("final")
            raise urllib.error.URLError("down")
        return rec(request, timeout)

    stop = threading.Event()
    main(
        [*ARGS, str(tmp_path)],
        ENV,
        tracker_factory=FakeTracker,
        source_opener=lambda live, env: StoppingSource(clock, stop, 2.0),
        opener=never,
        clock=clock,
        sleep=clock.sleep,
        stop=stop,
    )
    assert rec.attempts.count("final") == 3
