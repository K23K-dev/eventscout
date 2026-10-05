"""Turn a chat message into search changes, then merge them with earlier turns in code."""

import json
from datetime import date, timedelta
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.events.models import EventFilters

MAX_WINDOW_DAYS = 90
INSTRUCTIONS = """\
You turn messages about finding events around Georgia Tech and Atlanta into search changes.
Today is {today} in America/New_York.

A developer message gives, as data, the current search from earlier turns. Describe only what
the new message changes: use "unchanged" or null for anything it does not mention, so earlier
constraints carry over. Never add a constraint the person did not state.

- query: a short standalone description of what to look for, written for semantic search, such
  as "live jazz performances". Mention who it is for when they say so. Null when the message
  keeps the current topic, such as "only free ones".
- keywords: 1-4 distinctive words or short phrases likely to appear in matching listings. Null
  exactly when query is null.
- dates: this_weekend is the coming Saturday and Sunday; next_days takes days; between takes start
  and an inclusive end. Use "any" when they drop a date limit.
- region: "gt" only for Georgia Tech or on campus; "atlanta" only when they exclude campus. Every
  event is in the Atlanta area, so "in Atlanta" alone changes nothing.
- price: "free" only when they ask for free events; "any" when they drop that limit.
- location_kind: "online" or "in_person" only when they say so; "any" when they drop it.
- venue: a venue they name; "" to drop an earlier venue; null otherwise.
- clarification: a short question only when the message cannot become a search, such as a
  greeting. Otherwise null.
"""


class DateRequest(BaseModel):
    kind: Literal[
        "unchanged",
        "any",
        "today",
        "tomorrow",
        "this_weekend",
        "this_week",
        "next_week",
        "this_month",
        "next_month",
        "next_days",
        "between",
    ]
    days: int | None = Field(description="Number of days, for next_days")
    start: date | None = Field(description="First day, for between")
    end: date | None = Field(description="Last day, inclusive, for between")


class SearchIntent(BaseModel):
    """What one message changes; "unchanged" and null keep earlier turns' constraints."""

    query: str | None = Field(description="Standalone description to search by meaning")
    keywords: list[str] | None = Field(description="Distinctive words likely in listings")
    dates: DateRequest
    region: Literal["unchanged", "any", "gt", "atlanta"]
    price: Literal["unchanged", "any", "free"]
    location_kind: Literal["unchanged", "any", "in_person", "online"]
    venue: str | None = Field(description="Venue named in this message; empty to drop one")
    clarification: str | None = Field(description="Question to ask when no search is possible")


class SearchState(BaseModel):
    """The constraints in force after a turn; date_to is exclusive."""

    query: str
    keywords: list[str]
    date_from: date
    date_to: date
    region: Literal["gt", "atlanta"] | None
    price_status: Literal["free"] | None
    location_kind: Literal["in_person", "online"] | None
    venue: str | None

    def filters(self) -> EventFilters:
        return EventFilters(
            date_from=self.date_from,
            date_to=self.date_to,
            region=self.region,
            price_status=self.price_status,
            location_kind=self.location_kind,
            venue=self.venue,
        )

    def keyword_query(self) -> str:
        """Let any keyword match; a whole sentence would require every one of its words."""
        terms = (" ".join(keyword.replace('"', " ").split()) for keyword in self.keywords)
        return " or ".join(f'"{term}"' if " " in term else term for term in terms if term)[:200]


async def parse_intent(
    openai: AsyncOpenAI,
    model: str,
    message: str,
    state: SearchState | None,
    today: date,
) -> SearchIntent:
    """Ask the model what the message changes; the earlier search is data, not instructions."""
    context = {"current_search": state.model_dump(mode="json") if state else None}
    response = await openai.responses.parse(
        model=model,
        instructions=INSTRUCTIONS.format(today=f"{today:%A, %B} {today.day}, {today.year}"),
        input=[
            {"role": "developer", "content": json.dumps(context, ensure_ascii=False)},
            {"role": "user", "content": message},
        ],
        text_format=SearchIntent,
        reasoning={"effort": "low"},
        store=False,
        timeout=20,
    )
    if response.output_parsed is None:
        raise ValueError("The model returned no search changes")
    return response.output_parsed


def apply_intent(state: SearchState | None, intent: SearchIntent, today: date) -> SearchState:
    """Merge one message's changes into the earlier constraints."""
    current = state or SearchState(
        query="",
        keywords=[],
        date_from=today,
        date_to=today + timedelta(days=30),
        region=None,
        price_status=None,
        location_kind=None,
        venue=None,
    )
    date_from, date_to = resolve_dates(intent.dates, today, (current.date_from, current.date_to))
    merged: dict[str, Any] = {
        "query": current.query if intent.query is None else intent.query,
        "keywords": current.keywords if intent.keywords is None else intent.keywords,
        "date_from": date_from,
        "date_to": date_to,
        "region": _change(intent.region, current.region),
        "price_status": _change(intent.price, current.price_status),
        "location_kind": _change(intent.location_kind, current.location_kind),
        "venue": current.venue if intent.venue is None else intent.venue.strip() or None,
    }
    return SearchState.model_validate(merged)


def resolve_dates(
    request: DateRequest, today: date, current: tuple[date, date]
) -> tuple[date, date]:
    """Calendar dates for a request, as [start, end); relative ones use the reference date."""
    weekday = today.weekday()
    match request.kind:
        case "unchanged":
            start, end = current
        case "any":
            start, end = today, today + timedelta(days=30)
        case "today":
            start, end = today, today + timedelta(days=1)
        case "tomorrow":
            start, end = today + timedelta(days=1), today + timedelta(days=2)
        case "this_weekend" if weekday == 6:
            start, end = today, today + timedelta(days=1)
        case "this_weekend":
            start = today + timedelta(days=5 - weekday)
            end = start + timedelta(days=2)
        case "this_week":
            start, end = today, today + timedelta(days=7 - weekday)
        case "next_week":
            start = today + timedelta(days=7 - weekday)
            end = start + timedelta(days=7)
        case "this_month":
            start, end = today, _month_start(today, 1)
        case "next_month":
            start, end = _month_start(today, 1), _month_start(today, 2)
        case "next_days":
            start, end = today, today + timedelta(days=max(request.days or 7, 1))
        case "between":
            start = request.start or today
            end = max(request.end or start, start) + timedelta(days=1)
    return start, min(end, start + timedelta(days=MAX_WINDOW_DAYS))


def _change(change: str, current: str | None) -> str | None:
    return current if change == "unchanged" else None if change == "any" else change


def _month_start(day: date, months_ahead: int) -> date:
    month = day.month - 1 + months_ahead
    return date(day.year + month // 12, month % 12 + 1, 1)
