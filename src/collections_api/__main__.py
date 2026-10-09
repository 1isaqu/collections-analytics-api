"""``python -m collections_api``: read the settings and serve the API with uvicorn."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from collections_api.settings import Settings, SettingsError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="collections-api",
        description="Read the settings and serve the Collections Analytics API with uvicorn.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Address to bind (default: %(default)s)."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to bind (default: %(default)s)."
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional KEY=value file; real variables win (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env(dotenv=args.env_file)
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    import uvicorn

    from collections_api.main import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
