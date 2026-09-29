"""Compare keyword, vector, and hybrid results: `pnpm search "jazz"` or `--spot-checks`."""

import argparse
import asyncio
import io
import sys
from datetime import datetime, timedelta
from typing import Any

from openai import AsyncOpenAI
from pinecone import AsyncPinecone
from pydantic import ValidationError

from app.database import connect_database
from app.events.models import CATALOG_TIMEZONE, EventFilters, EventResponse
from app.events.repository import EventRepository
from app.ingestion.sources import SOURCES
from app.search.retrieval import SearchResults, search_events
from app.settings import Settings

# Keywords plus the structured filters each request implies, as intent parsing will produce them.
SPOT_CHECKS: list[tuple[str, dict[str, Any]]] = [
    ("jazz", {}),
    ("robotics", {}),
    ("career fair", {"region": "gt"}),
    ("machine learning seminar", {}),
    ("volunteer tree planting", {}),
    ("storytime for toddlers", {}),
    ("outdoor yoga", {"price_status": "free"}),
    ("startup pitch networking", {}),
    ("art gallery opening", {}),
    ("study break", {"audience": "Undergraduate students"}),
    ("coding workshop", {"location_kind": "online"}),
    ("live music", {"days": 7}),
    ("Falcons game", {}),
    ("puppet show for kids", {}),
    ("something relaxing to do outdoors", {}),
    ("underwater basket weaving championship", {}),
]
_OPTIONS = ("days", "region", "price_status", "location_kind", "venue", "audience")


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")  # Titles can hold characters a console lacks.
    parser = argparse.ArgumentParser(description="Compare keyword, vector, and hybrid search.")
    parser.add_argument("query", nargs="?", help="search keywords, e.g. 'jazz'")
    parser.add_argument("--spot-checks", action="store_true", help="run the saved spot checks")
    parser.add_argument("--days", type=int, help="window length from today (default: 30)")
    parser.add_argument("--region", choices=["gt", "atlanta"])
    parser.add_argument("--price-status", choices=["free", "paid", "conditional", "unknown"])
    parser.add_argument("--location-kind", choices=["in_person", "online", "hybrid", "unknown"])
    parser.add_argument("--venue")
    parser.add_argument("--audience")
    args = parser.parse_args()
    if bool(args.query) == args.spot_checks:
        parser.error("give either a search query or --spot-checks")
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
    requested = SPOT_CHECKS
    if args.query:
        options = {name: getattr(args, name) for name in _OPTIONS}
        requested = [(args.query, {k: v for k, v in options.items() if v is not None})]
    try:
        checks = [(query, _filters(options)) for query, options in requested]
    except ValidationError as exc:
        parser.error(f"invalid filters: {exc.errors()[0]['msg']}")
    try:
        # Psycopg's async connections require a selector loop on Windows.
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(_run(settings, checks))
    except Exception as exc:
        print(f"Search failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


def _filters(options: dict[str, Any]) -> EventFilters:
    today = datetime.now(CATALOG_TIMEZONE).date()
    return EventFilters(
        date_from=today,
        date_to=today + timedelta(days=options.get("days", 30)),
        **{name: value for name, value in options.items() if name != "days"},
    )


async def _run(settings: Settings, checks: list[tuple[str, EventFilters]]) -> None:
    assert settings.openai_api_key is not None and settings.pinecone_api_key is not None
    enabled = tuple(slug for slug, source in SOURCES.items() if source.enabled)
    async with (
        connect_database(settings) as connection,
        AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()) as openai,
        AsyncPinecone(api_key=settings.pinecone_api_key.get_secret_value()) as pinecone,
    ):
        async with await pinecone.index(settings.pinecone_index) as index, connection.transaction():
            await connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            repository = EventRepository(connection, enabled)
            for query, filters in checks:
                results = await search_events(repository, openai, index, query, filters)
                _print(query, filters, results)


def _print(query: str, filters: EventFilters, results: SearchResults) -> None:
    last_day = filters.end_date - timedelta(days=1)
    scope = [f"{filters.date_from:%b %d} - {last_day:%b %d}"]
    scope += [
        f"{name}={value}"
        for name in ("region", "price_status", "location_kind", "venue", "audience")
        if (value := getattr(filters, name)) is not None
    ]
    print(f"\n{query}  [{', '.join(scope)}]")
    if results.vector_error:
        print(f"  vector search failed ({results.vector_error}); hybrid is keyword-only")
    for label, events in (
        ("keyword", results.keyword),
        ("vector", results.vector),
        ("hybrid", results.hybrid),
    ):
        if not events:
            print(f"  {label:<8} (no results)")
        for position, event in enumerate(events, start=1):
            ranks = ""
            if label == "hybrid":
                keyword_rank = results.keyword_ranks.get(event.id, "-")
                vector_rank = results.vector_ranks.get(event.id, "-")
                ranks = f"  k{keyword_rank} v{vector_rank}"
            print(f"  {label if position == 1 else '':<8} {position}. {_describe(event)}{ranks}")


def _describe(event: EventResponse) -> str:
    start = event.start_date
    if start is None and event.starts_at is not None:
        start = event.starts_at.astimezone(CATALOG_TIMEZONE).date()
    when = f"{start:%b %d}" if start else "TBA"
    return f"{event.title[:60]:<60} {when:>6}  {event.price_status}"


if __name__ == "__main__":
    raise SystemExit(main())
