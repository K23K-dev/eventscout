"""Search events by chatting: `pnpm chat`, or `pnpm chat --script` for the saved conversations."""

import argparse
import asyncio
import io
import re
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from openai import AsyncOpenAI
from pinecone import AsyncPinecone

from app.assistant.graph import Services, Turn, TurnResult, answer, build_graph
from app.assistant.intent import DateRequest, SearchState, resolve_dates
from app.database import connect_database
from app.events.models import CATALOG_TIMEZONE, EventResponse
from app.events.repository import EventRepository
from app.ingestion.sources import SOURCES
from app.settings import Settings

# Saved conversations. Each message lists what should hold afterwards; dates are a relative
# kind, "next_days:N", or an inclusive "MM-DD..MM-DD" range.
SCRIPTS: list[list[tuple[str, dict[str, Any]]]] = [
    [
        ("any jazz this weekend?", {"dates": "this_weekend", "answered": True}),
        ("only free ones", {"price_status": "free", "dates": "this_weekend", "same_topic": True}),
        ("what about next month", {"dates": "next_month", "price_status": "free"}),
        ("tell me more about the second one", {"refers_to": [2], "answered": True}),
    ],
    [
        ("robotics talks at Georgia Tech", {"region": "gt"}),
        ("only online ones", {"location_kind": "online", "region": "gt", "same_topic": True}),
        ("actually in person or online is fine", {"location_kind": None, "region": "gt"}),
    ],
    [
        ("free outdoor yoga", {"price_status": "free"}),
        ("at Piedmont Park", {"venue": "piedmont", "price_status": "free", "same_topic": True}),
    ],
    [
        ("free events this weekend", {"price_status": "free", "dates": "this_weekend"}),
        ("comedy instead", {"price_status": "free", "dates": "this_weekend", "same_topic": False}),
    ],
    [
        ("free concerts next week", {"price_status": "free", "dates": "next_week"}),
        ("paid is fine too", {"price_status": None, "dates": "next_week", "same_topic": True}),
    ],
    [("things to do with kids in the next 3 days", {"dates": "next_days:3"})],
    [("Falcons games between October 10 and October 31", {"dates": "10-10..10-31"})],
    [("underwater basket weaving championship", {"admits_no_match": True, "max_cards": 2})],
    [("sunrise paddleboard yoga on a lake", {"admits_no_match": True, "max_cards": 2})],
    [("hi", {"clarify": True})],
    [("what about the second one?", {"clarify": True})],
]


_NO_MATCH = re.compile(
    r"\b(couldn.?t|could not|didn.?t|did not|no exact|none of|isn.?t any|aren.?t any)\b", re.I
)


@dataclass
class Conversation:
    state: SearchState | None = None
    shown: list[EventResponse] = field(default_factory=list)


@dataclass
class Session:
    graph: CompiledStateGraph[Turn, None, Turn, Turn]
    today: date

    async def turn(self, conversation: Conversation, message: str) -> TurnResult:
        """Answer one message and carry its search and shown events into the next."""
        started = time.perf_counter()
        result = await answer(
            self.graph, message, self.today, conversation.state, conversation.shown
        )
        print(f"  ({time.perf_counter() - started:.1f}s)")
        if result.clarification:
            print(f"  asks: {result.clarification}")
            return result
        if result.searched and result.search is not None:
            print(f"  search: {_summary(result.search)}")
        if result.broader:
            print(f"  searched more broadly for: {result.broader}")
        if result.reply:
            print(textwrap.fill(result.reply, 100, initial_indent="  ", subsequent_indent="  "))
        if result.note:
            print(f"  note: {result.note}")
        for position, event in enumerate(result.cards, start=1):
            print(f"  {position}. {_describe(event)}")
        conversation.state = result.search
        if result.searched:
            conversation.shown = result.cards
        return result


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")  # Titles can hold characters a console lacks.
    parser = argparse.ArgumentParser(description="Search events by chatting, with follow-ups.")
    parser.add_argument(
        "--script", action="store_true", help="run the saved conversations and check each turn"
    )
    args = parser.parse_args()
    settings = Settings()
    if missing := settings.missing("database_url", "openai_api_key", "pinecone_api_key"):
        parser.error(f"set {', '.join(missing)} in apps/api/.env")
    try:
        # Psycopg's async connections require a selector loop on Windows.
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(_chat(settings, scripted=args.script))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Chat failed ({type(exc).__name__}: {str(exc)[:200]}).", file=sys.stderr)
        return 1


async def _chat(settings: Settings, *, scripted: bool) -> int:
    assert settings.openai_api_key is not None and settings.pinecone_api_key is not None
    enabled = tuple(slug for slug, source in SOURCES.items() if source.enabled)
    async with (
        connect_database(settings) as connection,
        AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()) as openai,
        AsyncPinecone(api_key=settings.pinecone_api_key.get_secret_value()) as pinecone,
    ):
        async with await pinecone.index(settings.pinecone_index) as index:
            services = Services(
                connection,
                EventRepository(connection, enabled),
                openai,
                index,
                settings.intent_model,
                settings.answer_model,
            )
            session = Session(build_graph(services), datetime.now(CATALOG_TIMEZONE).date())
            if not scripted:
                conversation = Conversation()
                print("Ask about events around Georgia Tech and Atlanta; an empty line quits.")
                try:
                    while message := (await asyncio.to_thread(input, "\n> ")).strip():
                        await session.turn(conversation, message)
                except EOFError:
                    pass
                return 0
            met = total = 0
            for script in SCRIPTS:
                print("\n=== new conversation")
                conversation = Conversation()
                for message, expected in script:
                    print(f"\n> {message}")
                    before = conversation.state
                    result = await session.turn(conversation, message)
                    failures = _check(expected, result, before, session.today)
                    for failure in failures:
                        print(f"  MISMATCH {failure}")
                    met += len(expected) - len(failures)
                    total += len(expected)
            print(f"\n{met}/{total} expectations met")
            return 0 if met == total else 1


def _check(
    expected: dict[str, Any], result: TurnResult, before: SearchState | None, today: date
) -> list[str]:
    after = result.search
    failures = []
    for key, want in expected.items():
        got: Any
        match key:
            case "clarify":
                got = result.clarification is not None
            case "answered":
                got = result.reply is not None
            case "cards":
                got = len(result.cards)
            case "max_cards":
                got, want = len(result.cards) <= want, True
            case "admits_no_match":
                got = bool(_NO_MATCH.search(result.reply or ""))
            case "refers_to":
                got = result.intent.refers_to if result.intent else []
            case "same_topic":
                got = before is not None and after is not None and after.query == before.query
            case "dates":
                got = (after.date_from, after.date_to) if after else None
                want = _window(want, today)
            case "venue":
                got = after.venue if after else None
                if got and want and want.casefold() in got.casefold():
                    got = want
            case _:
                got = getattr(after, key, None)
        if got != want:
            failures.append(f"{key}: expected {want!r}, got {got!r}")
    return failures


def _window(spec: str, today: date) -> tuple[date, date]:
    if ".." in spec:
        first, last = (_upcoming(part, today) for part in spec.split(".."))
        return first, last + timedelta(days=1)
    kind, _, days = spec.partition(":")
    request = DateRequest.model_validate(
        {"kind": kind, "days": int(days) if days else None, "start": None, "end": None}
    )
    return resolve_dates(request, today, (today, today))


def _upcoming(month_day: str, today: date) -> date:
    month, day = (int(part) for part in month_day.split("-"))
    candidate = date(today.year, month, day)
    return candidate if candidate >= today else date(today.year + 1, month, day)


def _summary(state: SearchState) -> str:
    last_day = state.date_to - timedelta(days=1)
    parts = [
        f'"{state.query}"' if state.query else "anything",
        f"keywords: {state.keyword_query() or '-'}",
        f"{state.date_from:%b %d} - {last_day:%b %d}",
    ]
    parts += [
        f"{name}={value}"
        for name in ("region", "price_status", "location_kind", "venue")
        if (value := getattr(state, name))
    ]
    return " | ".join(parts)


def _describe(event: EventResponse) -> str:
    when = f"{event.first_day:%b %d}" if event.first_day else "TBA"
    return f"{event.title[:60]:<60} {when:>6}  {event.price_status}"


if __name__ == "__main__":
    raise SystemExit(main())
