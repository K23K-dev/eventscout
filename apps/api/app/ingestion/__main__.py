"""Run with `python -m app.ingestion`, or `pnpm ingest` from the repository root.

Imports the calendars, then summarizes new or changed events and updates the search index,
as the scheduled workflow does every six hours.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Coroutine
from dataclasses import asdict
from typing import Any

from app.enrichment.worker import run_enrichment
from app.indexing.worker import run_indexing
from app.ingestion.runner import run_import
from app.ingestion.sources import SOURCES
from app.settings import Settings


def _run[T](runner: asyncio.Runner, name: str, work: Coroutine[Any, Any, T]) -> T | None:
    """One stage; when it fails, the error is reported and the next stage still runs."""
    try:
        return runner.run(work)
    except Exception as exc:
        # Only the error type, so connection details stay out of the public workflow log.
        print(f"{name} failed ({type(exc).__name__}). Rerun to resume safely.", file=sys.stderr)
        return None


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("app").setLevel(logging.INFO)
    parser = argparse.ArgumentParser(
        description="Import GT and Atlanta calendar events, then summarize and index them."
    )
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
        "--dry-run", action="store_true", help="preview the import without a database connection"
    )
    parser.add_argument(
        "--reindex", action="store_true", help="re-embed every event, not only new or changed ones"
    )
    args = parser.parse_args()
    if not 1 <= args.days <= 90:
        parser.error("--days must be between 1 and 90")
    settings = Settings()
    if not args.dry_run and settings.missing("database_url"):
        parser.error("set EVENTSCOUT_DATABASE_URL in apps/api/.env, or use --dry-run")
    # Without these keys the import still runs; summaries and the search index wait (and fail).
    ai_keys = settings.missing("openai_api_key", "pinecone_api_key")
    if ai_keys and not args.dry_run:
        print(f"Skipping enrichment and indexing: set {', '.join(ai_keys)}.", file=sys.stderr)
    paid = not args.dry_run and not ai_keys
    # Psycopg's async connections require a selector loop on Windows.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            imported = _run(
                runner,
                "Import",
                run_import(
                    settings, days=args.days, dry_run=args.dry_run, source_names=args.source
                ),
            )
            enriched = _run(runner, "Enrichment", run_enrichment(settings)) if paid else None
            indexed = (
                _run(runner, "Indexing", run_indexing(settings, reindex=args.reindex))
                if paid
                else None
            )
    except KeyboardInterrupt:
        print("Interrupted. Rerun the command to resume safely.", file=sys.stderr)
        return 130
    stages = {"import": imported, "enrichment": enriched, "index": indexed}
    report = {name: asdict(result) if result else None for name, result in stages.items()}
    print(json.dumps(report, indent=2, ensure_ascii=True))
    failed = (
        imported is None
        or imported.failed
        or (not args.dry_run and any(not result or result.errors for result in (enriched, indexed)))
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
