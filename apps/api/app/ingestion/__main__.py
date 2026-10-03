"""Run with `python -m app.ingestion`, or `pnpm ingest` from the repository root."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict

from app.ingestion.runner import run_import
from app.ingestion.sources import SOURCES
from app.settings import Settings


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("app.ingestion").setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description="Import GT and Atlanta calendar events.")
    parser.add_argument(
        "--source",
        action="append",
        choices=list(SOURCES),
        help="import one calendar; repeat to select several (default: all calendars)",
    )
    parser.add_argument(
        "--days", type=int, default=90, help="rolling window, 1–90 days (default: 90)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="preview without a database connection"
    )
    args = parser.parse_args()
    if not 1 <= args.days <= 90:
        parser.error("--days must be between 1 and 90")
    settings = Settings()
    if not args.dry_run and settings.missing("database_url"):
        parser.error("set EVENTSCOUT_DATABASE_URL in apps/api/.env, or use --dry-run")
    try:
        # Psycopg's async connections require a selector loop on Windows.
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            report = runner.run(
                run_import(settings, days=args.days, dry_run=args.dry_run, source_names=args.source)
            )
    except KeyboardInterrupt:
        print("Import interrupted. Rerun the command to resume safely.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            f"Import failed ({type(exc).__name__}). Check database connectivity and rerun.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(asdict(report), indent=2, ensure_ascii=True))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
