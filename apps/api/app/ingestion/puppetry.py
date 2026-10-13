"""Published, individually ticketed performances at the Center for Puppetry Arts."""

import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import ATLANTA, image_url, localize, text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

CALENDAR_URL = "https://puppet.org/calendar"
_ACTIVITIES = {"Puppet Shows", "Special Events", "Workshops & Classes"}


def _link(card: Tag, selector: str) -> str:
    anchor = card.select_one(selector)
    if anchor is None or not isinstance(href := anchor.get("href"), str):
        raise ValueError("Missing published performance link")
    url = urljoin(CALENDAR_URL, href)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "puppet.org" or parts.username:
        raise ValueError("Unexpected performance link host")
    return url


def _performance(card: Tag) -> ParsedEvent:
    booking = _link(card, "a.calendar-event-popover__primary[href]")
    # The publisher uses these persistent Spektrix performance references in its
    # booking links. Calendar DOM IDs contain a date/index and are not identities.
    match = re.fullmatch(r"/Booking/([A-Za-z0-9]{16,80})/?", urlsplit(booking).path)
    if match is None:
        raise ValueError("Missing stable published booking identity")
    program = _link(card, "a.calendar-event-popover__secondary[href]")
    published = text(card, ".calendar-event-popover__date")
    summary = text(card, ".calendar-event-popover__summary")
    picture = card.select_one("img")
    return ParsedEvent(
        match[1],
        EventContent(
            title=text(card, "h3"),
            description=f"Program dates: {summary}" if summary else "",
            # The calendar provides start times, not individual performance ends.
            starts_at=localize(datetime.strptime(published, "%A, %B %d, %Y at %I:%M %p")),
            venue="Center for Puppetry Arts, 1404 Spring Street NW, Atlanta, GA 30309",
            location_kind="in_person",
            tags=[text(card, ".calendar-event-popover__type")],
            source_url=HttpUrl(program),
            registration_url=HttpUrl(booking),
        ),
        image_url=image_url(picture.get("src") if picture else None, CALENDAR_URL),
        # A program page lists every performance of its show.
        source_url_is_collection=True,
    )


def _month(html: bytes, month: datetime) -> SourceCollection:
    soup = BeautifulSoup(html, "html.parser")
    if month.strftime("%B %Y") not in {text(heading) for heading in soup.select("h2")}:
        raise ValueError("Calendar did not return the requested month")
    cards = soup.select("article.calendar-event-popover")
    if len(cards) > 2500:
        raise ValueError("Month calendar exceeds the 2500-card limit")
    result = SourceCollection(records_seen=len(cards))
    for card in cards:
        category = text(card, ".calendar-event-popover__type")
        # Daily museum/exhibition admission is an opening schedule, not a
        # separately scheduled performance or workshop.
        if category not in _ACTIVITIES:
            continue
        try:
            result.events.append(_performance(card))
        except (ValueError, TypeError) as exc:
            result.issues.append(ParseIssue(text(card, "h3") or None, str(exc)))
    return result


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Read each public month once, respecting the publisher's 30-second delay."""
    result = SourceCollection()
    current = window_start.astimezone(ATLANTA).replace(day=1)
    last = window_end.astimezone(ATLANTA).replace(day=1)
    if (last.year - current.year) * 12 + last.month - current.month > 3:
        raise ValueError("Puppetry calendar collection is limited to a 90-day window")
    events: dict[str, ParsedEvent] = {}
    conflicts: set[str] = set()
    first = current
    while (current.year, current.month) <= (last.year, last.month):
        if current != first:
            await asyncio.sleep(30)
        day = current.strftime("%Y-%m-01")
        feed_url = (
            f"{CALENDAR_URL}?display=month&month={day}&day={day}"
            "&audience=all&eventType=all&timeOfDay=all&accessibility=all"
        )
        try:
            parsed = _month(await fetch_bytes(client, feed_url), current)
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
                events[event.external_id] = event
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(
                ParseIssue(None, f"Month {day} failed ({type(exc).__name__}: {exc})")
            )
        current = current.replace(
            year=current.year + (current.month == 12), month=current.month % 12 + 1
        )
    result.events = [event for _, event in sorted(events.items())]
    return result
