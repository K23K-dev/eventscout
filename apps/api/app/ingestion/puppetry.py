"""Published, individually ticketed performances at the Center for Puppetry Arts."""

import asyncio
import re
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl, JsonValue

from app.ingestion.http import fetch_bytes
from app.ingestion.records import ParsedEvent, ParsedFeed, ParseIssue, SourceCollection, in_window
from app.storage.models import EventContent

CALENDAR_URL = "https://puppet.org/calendar"
_ZONE = ZoneInfo("America/New_York")
_ACTIVITIES = {"Puppet Shows", "Special Events", "Workshops & Classes"}


def _text(parent: Tag, selector: str) -> str:
    node = parent.select_one(selector)
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _link(card: Tag, selector: str) -> str:
    anchor = card.select_one(selector)
    if anchor is None or not isinstance(href := anchor.get("href"), str):
        raise ValueError("Missing published performance link")
    url = urljoin(CALENDAR_URL, href)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "puppet.org" or parts.username:
        raise ValueError("Unexpected performance link host")
    return url


def _performance(card: Tag, feed_url: str) -> ParsedEvent:
    booking = _link(card, "a.calendar-event-popover__primary[href]")
    # The publisher uses these persistent Spektrix performance references in its
    # booking links. Calendar DOM IDs contain a date/index and are not identities.
    match = re.fullmatch(r"/Booking/([A-Za-z0-9]{16,80})/?", urlsplit(booking).path)
    if match is None:
        raise ValueError("Missing stable published booking identity")
    program = _link(card, "a.calendar-event-popover__secondary[href]")
    title = _text(card, "h3")
    published_time = _text(card, ".calendar-event-popover__date")
    naive = datetime.strptime(published_time, "%A, %B %d, %Y at %I:%M %p")
    local = naive.replace(tzinfo=_ZONE)
    if (
        local.astimezone(UTC).astimezone(_ZONE).replace(tzinfo=None) != naive
        or local.utcoffset() != naive.replace(tzinfo=_ZONE, fold=1).utcoffset()
    ):
        raise ValueError("Ambiguous or nonexistent published performance time")
    summary = _text(card, ".calendar-event-popover__summary")
    category = _text(card, ".calendar-event-popover__type")
    return ParsedEvent(
        external_id=match[1],
        content=EventContent(
            title=title,
            description=f"Program dates: {summary}" if summary else "",
            starts_at=local,
            # The calendar provides start times, not individual performance ends.
            timezone=_ZONE.key,
            venue="Center for Puppetry Arts, 1404 Spring Street NW, Atlanta, GA 30309",
            location_kind="in_person",
            region="atlanta",
            tags=[category],
            source_url=HttpUrl(program),
            registration_url=HttpUrl(booking),
            status="cancelled" if re.search(r"\bcancel(?:led|ed)\b", title, re.I) else "scheduled",
        ),
        source_updated_at=None,
        raw_payload={
            "performance_id": match[1],
            "published_time": published_time,
            "program_dates": summary,
            "program_type": category,
            "performance_html": str(card),
            "source_url_is_collection": True,
            "feed_urls": [feed_url],
        },
    )


def _month(html: bytes, month: datetime, feed_url: str) -> ParsedFeed:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".calendar-shell") is None or not any(
        heading.get_text(" ", strip=True) == month.strftime("%B %Y")
        for heading in soup.select("h2")
    ):
        raise ValueError("Calendar did not return the requested month")
    cards = soup.select("article.calendar-event-popover")
    if len(cards) > 2500:
        raise ValueError("Month calendar exceeds the 2500-card limit")
    result = ParsedFeed([], len(cards), [])
    for card in cards:
        category = _text(card, ".calendar-event-popover__type")
        # Daily museum/exhibition admission is an opening schedule, not a
        # separately scheduled performance or workshop.
        if category not in _ACTIVITIES:
            continue
        try:
            result.events.append(_performance(card, feed_url))
        except (ValueError, TypeError) as exc:
            result.issues.append(ParseIssue(_text(card, "h3") or None, str(exc)))
    return result


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Read each public month once, respecting the publisher's 30-second delay."""
    result = SourceCollection([], 0, 0, [])
    current = window_start.astimezone(_ZONE).replace(day=1)
    last = window_end.astimezone(_ZONE).replace(day=1)
    if (last.year - current.year) * 12 + last.month - current.month > 3:
        raise ValueError("Puppetry calendar collection is limited to a 90-day window")
    events: dict[str, ParsedEvent] = {}
    conflicts: set[str] = set()
    while (current.year, current.month) <= (last.year, last.month):
        if result.requests:
            await asyncio.sleep(30)
        day = current.strftime("%Y-%m-01")
        feed_url = (
            f"{CALENDAR_URL}?display=month&month={day}&day={day}"
            "&audience=all&eventType=all&timeOfDay=all&accessibility=all"
        )
        result.requests += 1
        try:
            parsed = _month(await fetch_bytes(client, feed_url), current, feed_url)
            result.records_seen += parsed.records_seen
            result.issues.extend(parsed.issues)
            for event in parsed.events:
                if event.external_id in conflicts:
                    continue
                prior = events.get(event.external_id)
                if prior is not None:
                    if prior.content.content_hash() != event.content.content_hash():
                        result.issues.append(
                            ParseIssue(event.external_id, "Conflicting performance cards")
                        )
                        conflicts.add(event.external_id)
                        events.pop(event.external_id)
                        continue
                    prior_urls = prior.raw_payload.get("feed_urls", [])
                    known_urls = (
                        {url for url in prior_urls if isinstance(url, str)}
                        if isinstance(prior_urls, list)
                        else set()
                    )
                    urls: list[JsonValue] = [url for url in sorted(known_urls | {feed_url})]
                    event = replace(event, raw_payload={**event.raw_payload, "feed_urls": urls})
                events[event.external_id] = event
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(
                ParseIssue(None, f"Month {day} failed ({type(exc).__name__}: {exc})")
            )
        current = current.replace(
            year=current.year + (current.month == 12), month=current.month % 12 + 1
        )
    result.events = [
        event
        for _, event in sorted(events.items())
        if in_window(event.content, window_start, window_end)
    ]
    return result
