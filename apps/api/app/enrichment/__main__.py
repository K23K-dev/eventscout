"""Run with `python -m app.enrichment`, or `pnpm enrich` from the repository root."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict

from app.enrichment.worker import run_enrichment
from app.settings import Settings


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("app.enrichment").setLevel(logging.INFO)
    parser = argparse.ArgumentParser(description="Summarize and tag new or changed events.")
    parser.add_argument(
        "--limit", type=int, default=500, help="enrich at most this many events (default: 500)"
    )
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    settings = Settings()
    if missing := settings.missing("database_url", "openai_api_key"):
        parser.error(f"set {', '.join(missing)} in apps/api/.env")
    try:
        # Psycopg's async connections require a selector loop on Windows.
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            report = runner.run(run_enrichment(settings, limit=args.limit))
    except KeyboardInterrupt:
        print("Enrichment interrupted. Rerun the command to resume safely.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            f"Enrichment failed ({type(exc).__name__}). Check the database and OpenAI key "
            "and rerun.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(asdict(report), indent=2, ensure_ascii=True))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
