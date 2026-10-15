"""Collect actual showings from Carbonhouse's public venue calendars."""

import json
import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes, fetch_pages
from app.ingestion.parsing import (
    ATLANTA,
    cost_price,
    described_price,
    http_url,
    image_url,
    local_time,
    months,
    text,
)
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent


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
        value = " ".join(text(showing, part) for part in parts)
    return local_time(value)


def _parse_detail(html: bytes, url: str, default_venue: str) -> tuple[list[ParsedEvent], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = text(soup, "h1")
    description = text(soup, ".event_description")
    schema = _event_schema(soup)
    if not description and isinstance(schema.get("description"), str):
        description = text(schema["description"])
    venue = default_venue
    cost = ""
    for item in soup.select("li.item"):
        label = text(item, ".label")
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
        cancelled = schema.get("eventStatus") == "https://schema.org/EventCancelled" or any(
            re.fullmatch(r"cancel(?:led|ed)[.!]?", string, re.I)
            for string in showing.stripped_strings
        )
        performer = schema.get("performer")
        sports = isinstance(performer, dict) and performer.get("@type") == "SportsTeam"
        events.append(
            ParsedEvent(
                f"showing:{identity}",
                EventContent(
                    title=title,
                    description=description,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    venue=venue,
                    location_kind="in_person",
                    price_status=price_status,
                    price_details=price_details,
                    tags=["Sports" if sports else "Live performance"],
                    source_url=HttpUrl(url),
                    registration_url=HttpUrl(registration) if registration else None,
                    status="cancelled" if cancelled else "scheduled",
                ),
                image_url=image_url(schema.get("image")),
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
    """Read the month calendars' JSON, then each detail page; every showing is its own event."""
    result = SourceCollection()
    parts = urlsplit(calendar_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    first_day, last_day = (
        moment.astimezone(ATLANTA).date() for moment in (window_start, window_end)
    )
    urls: set[str] = set()
    for month in months(window_start, window_end):
        with result.issue_if_fails(f"{month:%B %Y} calendar"):
            data = json.loads(
                await fetch_bytes(
                    client,
                    f"{origin}/events/calendar/{month.year}/{month.month}",
                    params={"v": "2", "detail_partial": "events/partials/calendar_detail"},
                )
            )
            for day, markup in data.items() if isinstance(data, dict) else []:
                if not first_day <= datetime.strptime(day, "%m-%d-%Y").date() <= last_day:
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
    if len(urls) > 300:
        result.issues.append(ParseIssue(None, "Calendar exceeds the 300-detail limit"))
        return result
    ordered = sorted(urls)
    for url, page in zip(ordered, await fetch_pages(client, ordered), strict=True):
        with result.issue_if_fails(url):
            if isinstance(page, BaseException):
                raise page
            events, warnings = _parse_detail(page, url, default_venue)
            result.warnings.extend(warnings)
            result.records_seen += len(events)
            result.events.extend(events)
    result.warnings = sorted(set(result.warnings))
    return result
