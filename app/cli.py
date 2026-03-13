from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GitHub Stars Manager.")
    parser.add_argument("--host", default=os.environ.get("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT", "17001")))
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable auto-reload.",
    )
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )

