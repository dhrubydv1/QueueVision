"""``python -m api``: run the QueueVision API with uvicorn."""

from __future__ import annotations

import argparse

from api.settings import ApiSettings


def main(argv: list[str] | None = None) -> None:
    defaults = ApiSettings.from_env()
    parser = argparse.ArgumentParser(prog="python -m api", description="Run the QueueVision upload API.")
    parser.add_argument("--host", default=defaults.host, help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=defaults.port, help="port (default: %(default)s)")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run("api.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
