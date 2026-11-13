"""``python -m api``: run the QueueVision API with uvicorn."""

from __future__ import annotations

import argparse
import logging
import os

from api.settings import ApiSettings, SettingsError, is_loopback

LOGGER = logging.getLogger("queuevision.api")
GRACEFUL_SHUTDOWN_S = 5  # open MJPEG streams must not block Ctrl+C until the video ends


def warn_if_exposed(host: str) -> bool:
    """Log a prominent warning when ``host`` is not loopback; True when it warned."""
    if is_loopback(host):
        return False
    LOGGER.warning(
        "SECURITY WARNING: binding to %r. The QueueVision API has NO authentication, no TLS and no "
        "access control; anyone who can reach this address can upload, read and delete runs. "
        "Do not expose it beyond this machine.",
        host,
    )
    return True


def main(argv: list[str] | None = None) -> None:
    try:
        defaults = ApiSettings.from_env()
    except SettingsError as error:
        raise SystemExit(f"error: {error}") from error
    parser = argparse.ArgumentParser(prog="python -m api", description="Run the QueueVision upload API.")
    parser.add_argument("--host", default=defaults.host, help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=defaults.port, help="port (default: %(default)s)")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    args = parser.parse_args(argv)

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    warn_if_exposed(args.host)
    # The app builds its settings from the environment (also in --reload children), so the CLI
    # values must be visible there for the allowed-origins check to match the real address.
    os.environ["QUEUEVISION_HOST"] = args.host
    os.environ["QUEUEVISION_PORT"] = str(args.port)

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )


if __name__ == "__main__":
    main()
