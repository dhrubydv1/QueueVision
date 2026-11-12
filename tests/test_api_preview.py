"""Live preview tests: head-blurred JPEG / MJPEG for processing runs (torch-free)."""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from ai_engine.detection.types import PersonDetection
from api import preview as preview_module
from api.preview import PreviewStore, mjpeg_part, render_preview
from tests.test_api import WAIT, clip, make_client, settings, upload, wait_status  # noqa: F401
from tests.test_api_jobs import GatedTracker


def wait_for_preview(client, video_id: int) -> None:
    deadline = time.time() + WAIT
    while time.time() < deadline:
        if client.get(f"/api/videos/{video_id}/preview.jpg").status_code == 200:
            return
        time.sleep(0.05)
    raise AssertionError("no preview frame appeared")


def test_render_preview_blurs_heads_only() -> None:
    image = np.random.default_rng(0).integers(0, 256, (160, 200, 3), dtype=np.uint8)
    original = image.copy()
    out = render_preview(image, [PersonDetection(bbox=(60, 30, 100, 110), confidence=0.9, track_id=1)])
    assert np.array_equal(image, original)
    assert not np.array_equal(out[30:46, 60:100], original[30:46, 60:100])
    mask = np.ones(image.shape[:2], dtype=bool)
    mask[30:46, 60:100] = False
    assert np.array_equal(out[mask], original[mask])


def test_render_preview_downscales_wide_frames() -> None:
    out = render_preview(np.zeros((1080, 1920, 3), dtype=np.uint8), [])
    assert out.shape[:2] == (540, 960)


def test_store_rate_limit() -> None:
    store = PreviewStore()
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    assert store.offer(1, image, [], 0.0) is True
    first = store.latest(1)
    assert store.offer(1, image, [], 0.1) is False
    assert store.latest(1)[0] == first[0]
    assert store.offer(1, image, [], 0.25) is True
    assert store.latest(1)[0] == first[0] + 1
    assert store.latest(1)[1].startswith(b"\xff\xd8")
    store.drop(1)
    assert store.latest(1) is None


def test_preview_routes_404_rules(settings, clip) -> None:  # noqa: F811
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=0)) as client:
        for suffix in ("preview.jpg", "preview.mjpg"):
            assert client.get(f"/api/videos/99/{suffix}").status_code == 404
        first = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        second = upload(client, clip).json()["video_id"]  # queued behind the first
        for suffix in ("preview.jpg", "preview.mjpg"):
            queued = client.get(f"/api/videos/{second}/{suffix}")
            assert (
                queued.status_code == 404 and queued.json()["detail"] == "no preview: run is not processing"
            )
            yet = client.get(f"/api/videos/{first}/{suffix}")
            assert yet.status_code == 404 and yet.json()["detail"] == "no preview frame yet"
        release.set()
        wait_status(client, second)
        for suffix in ("preview.jpg", "preview.mjpg"):
            done = client.get(f"/api/videos/{first}/{suffix}")
            assert done.status_code == 404 and done.json()["detail"] == "no preview: run is not processing"


def test_preview_jpg_while_processing(settings, clip) -> None:  # noqa: F811
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=3)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        response = client.get(f"/api/videos/{video_id}/preview.jpg")
        release.set()
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert response.content.startswith(b"\xff\xd8")
        assert cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR) is not None
        wait_status(client, video_id)


def test_mjpeg_boundary_format(settings, clip) -> None:  # noqa: F811
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=3)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        try:
            with client.stream("GET", f"/api/videos/{video_id}/preview.mjpg") as response:
                assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
                chunk = next(response.iter_bytes())
        finally:
            release.set()
        head, _, _ = chunk.partition(b"\r\n\r\n")
        assert head.startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ")
        length = int(head.rsplit(b": ", 1)[1])
        body = chunk[len(head) + 4 :]
        assert body.startswith(b"\xff\xd8") and len(body) >= length
        assert chunk == mjpeg_part(body[:length])
        wait_status(client, video_id)


def test_mjpeg_ends_when_run_ends(settings, clip) -> None:  # noqa: F811
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=3)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        with client.stream("GET", f"/api/videos/{video_id}/preview.mjpg") as response:
            iterator = response.iter_bytes()
            assert next(iterator)
            release.set()
            done = threading.Event()

            def drain() -> None:
                for _ in iterator:
                    pass
                done.set()

            threading.Thread(target=drain, daemon=True).start()
            assert done.wait(3.0), "stream did not end after the run ended"
        wait_status(client, video_id)
        assert client.get(f"/api/videos/{video_id}/preview.jpg").status_code == 404


def test_mjpeg_ends_when_server_stops(settings, clip) -> None:  # noqa: F811
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=3)) as client:
        jobs = client.app.state.jobs
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        try:
            with client.stream("GET", f"/api/videos/{video_id}/preview.mjpg") as response:
                iterator = response.iter_bytes()
                assert next(iterator)
                done = threading.Event()

                def drain() -> None:
                    for _ in iterator:
                        pass
                    done.set()

                threading.Thread(target=drain, daemon=True).start()
                jobs._stop.set()  # what jobs.stop() does first; the gated run is still blocked
                assert jobs.stopping
                assert done.wait(3.0), "stream did not end when the job manager was stopping"
        finally:
            release.set()


def test_main_sets_graceful_shutdown_timeout(monkeypatch) -> None:
    import uvicorn

    from api.__main__ import main

    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs))
    main(["--port", "8123"])
    assert captured["timeout_graceful_shutdown"] == 5


def test_preview_blurred_even_when_blur_off(settings, clip, monkeypatch) -> None:  # noqa: F811
    seen: list[np.ndarray] = []
    real = preview_module.render_preview

    def spy(image, people):
        seen.append(image.copy())
        return real(image, people)

    monkeypatch.setattr(preview_module, "render_preview", spy)
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=3)) as client:
        jobs = client.app.state.jobs
        video_id = upload(client, clip, blur_heads="false").json()["video_id"]
        assert reached.wait(WAIT)
        wait_for_preview(client, video_id)
        latest = jobs.preview.latest(video_id)
        release.set()
        wait_status(client, video_id)
    raw = seen[latest[0] - 1]  # the spy runs once per encode: input of the stored frame
    stored = cv2.imdecode(np.frombuffer(latest[1], np.uint8), cv2.IMREAD_COLOR)
    assert stored.shape == raw.shape
    # the input head box (frame 0 person at x 120..160, y 30..46) is still sharp: the box outline
    # drawn on the annotated frame gives it strong edges
    head = (slice(30, 46), slice(120, 160))
    assert np.abs(np.diff(raw[head].astype(int), axis=1)).max() > 60
    _ok, buffer = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 70])
    unblurred = cv2.imdecode(buffer, cv2.IMREAD_COLOR)  # what the same JPEG would show without blur
    diff = np.abs(stored.astype(int) - unblurred.astype(int))
    assert diff[head].mean() > 2.0  # the head region differs from the sharp input
    outside = np.ones(raw.shape[:2], dtype=bool)
    outside[head] = False
    assert diff[outside].mean() < 0.5  # rest of the frame matches an unblurred encode
