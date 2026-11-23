"""Demo command: ``python -m worker.ctl start|stop|status <camera> [--api URL]``."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_API = "http://127.0.0.1:8000"


class CtlError(RuntimeError):
    pass


def _call(opener: Callable[..., Any], method: str, url: str) -> Any:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        response = opener(request, timeout=30)
        try:
            body = response.read()
        finally:
            response.close()
    except urllib.error.HTTPError as error:
        detail = ""
        with contextlib.suppress(Exception):
            detail = str(json.loads(error.read().decode("utf-8")).get("detail", ""))
        raise CtlError(f"API answered {error.code}{': ' + detail if detail else ''}") from None
    except OSError as error:
        raise CtlError(f"cannot reach the API at {url.split('/api/')[0]} ({type(error).__name__})") from None
    return json.loads(body) if body else None


def _sessions(opener: Callable[..., Any], api: str, camera: str | None) -> list[dict]:
    sessions = _call(opener, "GET", f"{api}/api/live") or []
    return [s for s in sessions if camera is None or s.get("camera_id") == camera]


def main(
    argv: list[str] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    out: Any = None,
) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(
        prog="python -m worker.ctl", description="Start, stop or list live cameras."
    )
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("camera", nargs="?", help="camera id (required for start and stop)")
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args(argv)
    api = args.api.rstrip("/")
    try:
        if args.action != "status" and not args.camera:
            raise CtlError(f"{args.action} needs a camera id")
        if args.action == "start":
            run = _call(opener, "POST", f"{api}/api/cameras/{args.camera}/live")
            video_id = run["id"]
            print(f"video id {video_id}", file=out)
            print(f"Live page: {api}/live?id={video_id}", file=out)
        elif args.action == "stop":
            found = _sessions(opener, api, args.camera)
            if not found:
                raise CtlError(f"no live session for camera {args.camera}")
            for session in found:
                _call(opener, "POST", f"{api}/api/videos/{session['video_id']}/stop")
                print(f"stopped video id {session['video_id']}", file=out)
        else:
            found = _sessions(opener, api, args.camera)
            if not found:
                print("no live sessions", file=out)
            for session in found:
                print(
                    f"video id {session['video_id']}  camera {session.get('camera_id')}  "
                    f"{session.get('source_kind')}  fps {session.get('processing_fps')}  "
                    f"heartbeat {session.get('heartbeat_age_s')} s ago  {api}/live?id={session['video_id']}",
                    file=out,
                )
    except CtlError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
