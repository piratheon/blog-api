"""Local dev server: ``uv run fieldnotes-api``."""

from __future__ import annotations

import argparse

from .app import create_app
from .config import Config
from .db import create_schema, init_engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fieldnotes-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--init-db", action="store_true", help="create tables then exit")
    args = parser.parse_args(argv)

    config = Config()
    app = create_app(config)

    if args.init_db:
        create_schema()
        print("schema created")
        return 0

    for problem in config.warn_if_insecure():
        print(f"! {problem}")

    print(f"listening on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
