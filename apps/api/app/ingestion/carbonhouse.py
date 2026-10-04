"""Collect actual showings from Carbonhouse's public venue calendars."""

import asyncio
import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from dateutil import parser
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    CANCELLED_TITLE,
    cost_price,
    described_price,
    http_url,
    image_url,
    issue_message,
    localize,
    text,
)
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

_ZONE = ZoneInfo("America/New_York")


def _url(value: object, base: str) -> str:
    return str(http_url(urljoin(base, unescape(str(value)))))


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
        if match := re.search(
            r"[A-Za-z]+\s+\d{1,2}\s+\d{4}\s+at\s+\d{1,2}:\d{2}\s*[AP]M", title, re.I
        ):
            value = match[0]
            break
    else:
        parts = (".m-date__month", ".m-date__day", ".m-date__year", ".m-date__hour")
        value = " ".join(text(showing.select_one(part)) for part in parts)
    return localize(parser.parse(value), _ZONE).astimezone(UTC)


def _parse_detail(html: bytes, url: str, default_venue: str) -> tuple[list[ParsedEvent], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = text(soup.select_one("h1"))
    description = text(soup.select_one(".event_description"))
    schema = _event_schema(soup)
    if not description and isinstance(schema.get("description"), str):
        description = text(schema["description"])
    venue = default_venue
    cost = ""
    for item in soup.select("li.item"):
        label = text(item.select_one(".label"))
        if label.casefold() == "venue":
            venue = text(item).removeprefix(label).strip() or venue
        elif label.casefold() in {"ticket prices", "ticket price", "price"}:
            cost = text(item).removeprefix(label).strip()
    price_status, price_details = cost_price(cost) if cost else described_price(description)
    showings = soup.select("[data-showing-id], [id^='showing_']")
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
        cancelled = (
            schema.get("eventStatus") == "https://schema.org/EventCancelled"
            or CANCELLED_TITLE.match(title) is not None
            or any(
                re.fullmatch(r"cancel(?:led|ed)[.!]?", string, re.I)
                for string in showing.stripped_strings
            )
        )
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
                    price_details=price_details,
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
                    "event_schema": schema,
                    "image_url": image_url(schema.get("image")),
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
    result = SourceCollection([], 0, [])
    parts = urlsplit(calendar_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    current = window_start.astimezone(_ZONE).replace(day=1)
    last = window_end.astimezone(_ZONE).replace(day=1)
    urls: set[str] = set()
    while (current.year, current.month) <= (last.year, last.month):
        try:
            data = json.loads(
                await fetch_bytes(
                    client,
                    f"{origin}/events/calendar/{current.year}/{current.month}",
                    params={"v": "2", "detail_partial": "events/partials/calendar_detail"},
                )
            )
            for day, markup in data.items() if isinstance(data, dict) else []:
                published_day = datetime.strptime(day, "%m-%d-%Y").date()
                if (
                    not window_start.astimezone(_ZONE).date()
                    <= published_day
                    <= window_end.astimezone(_ZONE).date()
                ):
                    continue
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
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError) as exc:
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
            try:
                return _parse_detail(await fetch_bytes(client, url), url, default_venue)
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
            result.events.append(event)
    result.events.sort(key=lambda event: event.external_id)
    result.warnings = sorted(set(result.warnings))
    return result
