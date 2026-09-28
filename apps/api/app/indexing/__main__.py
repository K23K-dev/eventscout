"""Run with `python -m app.indexing`, or `pnpm index` from the repository root."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict

from app.indexing.worker import run_indexing
from app.settings import Settings


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("app.indexing").setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description="Embed new or changed events into Pinecone.")
    parser.add_argument(
        "--limit", type=int, help="process at most this many queued jobs (default: all)"
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    settings = Settings()
    missing = [
        name
        for name, value in (
            ("EVENTSCOUT_DATABASE_URL", settings.database_url),
            ("EVENTSCOUT_OPENAI_API_KEY", settings.openai_api_key),
            ("EVENTSCOUT_PINECONE_API_KEY", settings.pinecone_api_key),
        )
        if value is None or not value.get_secret_value().strip()
    ]
    if missing:
        parser.error(f"set {', '.join(missing)} in apps/api/.env")
    try:
        # Psycopg's async connections require a selector loop on Windows.
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            report = runner.run(run_indexing(settings, limit=args.limit))
    except KeyboardInterrupt:
        print("Indexing interrupted. Rerun the command to resume safely.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            f"Indexing failed ({type(exc).__name__}). Check the database and provider keys "
            "and rerun.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(asdict(report), indent=2, ensure_ascii=True))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
