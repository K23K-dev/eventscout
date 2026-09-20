"""Collect actual showings from Carbonhouse's public venue calendars."""

import asyncio
import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import http_url, issue_message, localize, node_text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection, in_window
from app.storage.models import EventContent

_ZONE = ZoneInfo("America/New_York")


def _text(node: Tag | None) -> str:
    return node_text(node, decode_entities=True)


def _url(value: object, base: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing public event link")
    return str(http_url(urljoin(base, unescape(value))))


def _event_schema(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") in {
                "Event",
                "MusicEvent",
                "SportsEvent",
            }:
                return item
    return {}


def _showing_start(showing: Tag) -> datetime | None:
    for anchor in showing.select("[title]"):
        title = str(anchor.get("title", ""))
        if re.search(r"\bat\s+(?:TBA|TBD)\b", title, re.I):
            return None
        match = re.search(
            r"(?:for )?([A-Za-z]+\s+\d{1,2}\s+\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)", title, re.I
        )
        if match:
            day, clock = match.groups()
            value = " ".join(day.split()) + " " + clock.replace(" ", "")
            break
    else:
        month = _text(showing.select_one(".m-date__month"))
        day = _text(showing.select_one(".m-date__day"))
        year = _text(showing.select_one(".m-date__year")).strip(", ")
        clock = re.sub(r"^at\s*", "", _text(showing.select_one(".m-date__hour")), flags=re.I)
        value = f"{month} {day} {year} {clock.replace(' ', '')}"
    return localize(datetime.strptime(value, "%B %d %Y %I:%M%p"), _ZONE).astimezone(UTC)


def _parse_detail(
    html: bytes, url: str, calendar_url: str, default_venue: str, window_start: datetime
) -> tuple[list[ParsedEvent], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.select_one("h1"))
    if not title:
        raise ValueError("Missing event title")
    description = _text(soup.select_one(".event_description"))
    schema = _event_schema(soup)
    if not description and isinstance(schema.get("description"), str):
        description = _text(BeautifulSoup(schema["description"], "html.parser"))
    venue = default_venue
    cost = ""
    for item in soup.select("li.item"):
        label = _text(item.select_one(".label"))
        if label.casefold() == "venue":
            venue = _text(item).removeprefix(label).strip() or venue
        elif label.casefold() in {"ticket prices", "ticket price", "price"}:
            cost = _text(item).removeprefix(label).strip()
    price_status: Literal["free", "paid", "conditional", "unknown"] = "unknown"
    if cost:
        if re.search(r"\b(?:members?|free.*(?:with|for))\b", cost, re.I):
            price_status = "conditional"
        elif cost.casefold() in {"free", "$0", "0"}:
            price_status = "free"
        elif re.search(r"\$\s*\d", cost):
            price_status = "paid"
    elif re.search(r"\b(?:tickets|admission) (?:are|is) free\b", description, re.I):
        price_status, cost = "free", "Tickets/admission explicitly listed as free"
    showings = soup.select("[data-showing-id], [id^='showing_']")
    if not showings:
        last_time = schema.get("endDate") or schema.get("startDate")
        if isinstance(last_time, str):
            ending = datetime.fromisoformat(last_time)
            if ending.tzinfo is not None and ending <= window_start:
                return [], []
        raise ValueError("No individual showing identities published on this page")
    events: list[ParsedEvent] = []
    warnings: list[str] = []
    for showing in showings:
        identity = str(
            showing.get("data-showing-id") or str(showing.get("id", "")).removeprefix("showing_")
        )
        if not identity.isdigit():
            raise ValueError("Missing stable showing ID")
        starts_at = _showing_start(showing)
        if starts_at is None:
            warnings.append(
                f"{title}: showing {identity} has a TBA start; excluded until scheduled."
            )
            continue
        ends_at = None
        # A series-wide JSON-LD end is not the duration of each performance.
        if (
            len(showings) == 1
            and isinstance(schema.get("startDate"), str)
            and isinstance(schema.get("endDate"), str)
        ):
            schema_start = datetime.fromisoformat(schema["startDate"])
            schema_end = datetime.fromisoformat(schema["endDate"])
            if (
                schema_start.tzinfo is not None
                and schema_end.tzinfo is not None
                and schema_start == starts_at
                and 0 < (schema_end - schema_start).total_seconds() <= 86400
            ):
                ends_at = schema_end
        ticket = showing.select_one("a.tickets[href]")
        registration = _url(ticket.get("href"), url) if ticket else None
        cancelled = re.search(r"\bcancel(?:led|ed)\b", _text(showing), re.I) is not None
        cancelled = cancelled or schema.get("eventStatus") == "https://schema.org/EventCancelled"
        performer = schema.get("performer")
        sports = isinstance(performer, dict) and performer.get("@type") == "SportsTeam"
        events.append(
            ParsedEvent(
                external_id=f"showing:{identity}",
                content=EventContent(
                    title=title,
                    description=description,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    venue=venue,
                    location_kind="in_person",
                    region="atlanta",
                    price_status=price_status,
                    price_details=cost or None,
                    tags=["Sports" if sports else "Live performance"],
                    source_url=HttpUrl(url),
                    registration_url=HttpUrl(registration) if registration else None,
                    status="cancelled" if cancelled else "scheduled",
                ),
                source_updated_at=None,
                raw_payload={
                    "showing_id": identity,
                    "showing_html": str(showing),
                    "url": url,
                    "feed_urls": [calendar_url],
                    "event_schema": schema,
                },
            )
        )
    return events, warnings


async def collect(
    client: httpx.AsyncClient,
    *,
    calendar_url: str,
    default_venue: str,
    window_start: datetime,
    window_end: datetime,
) -> SourceCollection:
    """Read published month-calendar JSON and detail pages with two requests at once.

    The month endpoint is the same public feed used by the publisher's calendar.
    Stable showing IDs keep separate performances distinct without treating a
    multiweek production's first and last dates as one continuous event.
    """
    result = SourceCollection([], 0, 0, [])
    parts = urlsplit(calendar_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    current = window_start.astimezone(_ZONE).replace(day=1)
    last = window_end.astimezone(_ZONE).replace(day=1)
    urls: set[str] = set()
    while (current.year, current.month) <= (last.year, last.month):
        result.requests += 1
        try:
            data = json.loads(
                await fetch_bytes(
                    client,
                    f"{origin}/events/calendar/{current.year}/{current.month}",
                    params={"v": "2", "detail_partial": "events/partials/calendar_detail"},
                )
            )
            if data != [] and not isinstance(data, dict):
                raise ValueError("Expected a month calendar object")
            for day, markup in data.items() if isinstance(data, dict) else []:
                published_day = datetime.strptime(day, "%m-%d-%Y").date()
                if (
                    not window_start.astimezone(_ZONE).date()
                    <= published_day
                    <= window_end.astimezone(_ZONE).date()
                ):
                    continue
                if not isinstance(markup, str):
                    raise ValueError("Invalid month calendar markup")
                for anchor in BeautifulSoup(markup, "html.parser").select("h3 a[href]"):
                    url = _url(anchor.get("href"), origin)
                    if urlsplit(url).hostname != parts.hostname:
                        raise ValueError("Calendar detail unexpectedly leaves the publisher")
                    if "/events/detail/" not in urlsplit(url).path:
                        result.warnings.append(
                            f"Excluded program overview without individual showing IDs: {url}"
                        )
                        continue
                    urls.add(url)
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(ParseIssue(None, f"Month calendar failed ({type(exc).__name__})"))
        current = current.replace(
            year=current.year + (current.month == 12), month=current.month % 12 + 1
        )
    if len(urls) > 300:
        result.issues.append(ParseIssue(None, "Calendar exceeds the 300-detail limit"))
        return result
    semaphore = asyncio.Semaphore(2)

    async def detail(url: str) -> tuple[list[ParsedEvent], list[str]] | ParseIssue:
        async with semaphore:
            result.requests += 1
            try:
                return _parse_detail(
                    await fetch_bytes(client, url), url, calendar_url, default_venue, window_start
                )
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError) as exc:
                return ParseIssue(url, issue_message(exc))

    identities: set[str] = set()
    for response in await asyncio.gather(*(detail(url) for url in sorted(urls))):
        if isinstance(response, ParseIssue):
            result.issues.append(response)
            continue
        events, warnings = response
        result.warnings.extend(warnings)
        result.records_seen += len(events)
        for event in events:
            if event.external_id in identities:
                result.issues.append(ParseIssue(event.external_id, "Repeated showing ID"))
                continue
            identities.add(event.external_id)
            if in_window(event.content, window_start, window_end):
                result.events.append(event)
    result.events.sort(key=lambda event: event.external_id)
    result.warnings = sorted(set(result.warnings))
    return result
