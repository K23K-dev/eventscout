"""Published, individually ticketed performances at the Center for Puppetry Arts."""

import asyncio
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import ATLANTA, image_url, months, text
from app.ingestion.records import ParsedEvent, SourceCollection
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
    # The booking link's Spektrix ID is stable; the calendar's DOM IDs are not.
    match = re.fullmatch(r"/Booking/([A-Za-z0-9]{16,80})/?", urlsplit(booking).path)
    if match is None:
        raise ValueError("Missing stable published booking identity")
    program = _link(card, "a.calendar-event-popover__secondary[href]")
    published = text(card, ".calendar-event-popover__date")
    starts_at = datetime.strptime(published, "%A, %B %d, %Y at %I:%M %p").replace(tzinfo=ATLANTA)
    summary = text(card, ".calendar-event-popover__summary")
    picture = card.select_one("img")
    return ParsedEvent(
        match[1],
        EventContent(
            title=text(card, "h3"),
            description=f"Program dates: {summary}" if summary else "",
            # The calendar provides start times, not individual performance ends.
            starts_at=starts_at,
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


def _month(html: bytes, month: date, result: SourceCollection) -> None:
    soup = BeautifulSoup(html, "html.parser")
    if f"{month:%B %Y}" not in {text(heading) for heading in soup.select("h2")}:
        raise ValueError("Calendar did not return the requested month")
    cards = soup.select("article.calendar-event-popover")
    if len(cards) > 2500:
        raise ValueError("Month calendar exceeds the 2500-card limit")
    result.records_seen += len(cards)
    for card in cards:
        # Daily museum admission is an opening schedule, not a performance or workshop.
        if text(card, ".calendar-event-popover__type") in _ACTIVITIES:
            with result.issue_if_fails(text(card, "h3") or None):
                result.events.append(_performance(card))


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Read each public month once, respecting the publisher's 30-second delay."""
    result = SourceCollection()
    for index, month in enumerate(months(window_start, window_end)):
        if index:
            await asyncio.sleep(30)
        day = f"{month:%Y-%m-%d}"
        feed_url = (
            f"{CALENDAR_URL}?display=month&month={day}&day={day}"
            "&audience=all&eventType=all&timeOfDay=all&accessibility=all"
        )
        with result.issue_if_fails(f"{month:%B %Y}"):
            _month(await fetch_bytes(client, feed_url), month, result)
    return result
