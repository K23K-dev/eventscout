"""Public HTML calendars for local conservation and volunteering events."""

import re
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import local_time, location_kind, months, text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

TREES_URL = "https://www.treesatlanta.org/get-involved/events/"
SOUTH_FORK_URL = "https://southforkconservancy.org/events"


def _tree_event(card: Tag, month: date) -> ParsedEvent:
    anchor = card.find_parent("a", href=True)
    if anchor is None:
        raise ValueError("Missing event URL")
    url = urljoin(TREES_URL, str(anchor["href"]))
    identity = re.search(r"-([a-zA-Z0-9]{18})/?$", urlsplit(url).path)
    if urlsplit(url).hostname != "www.treesatlanta.org" or identity is None:
        raise ValueError("Unrecognized Trees Atlanta event identity")
    parts = text(card, ".time-place").split("|")
    if len(parts) not in (2, 3):
        raise ValueError("Unrecognized event date and venue")
    date_text, time_text = (part.strip() for part in parts[:2])
    venue = parts[2].strip() if len(parts) == 3 else None
    times = re.fullmatch(
        r"(\d{1,2}(?::\d{2})?[ap]m)(?:\s*-\s*(\d{1,2}(?::\d{2})?[ap]m))?", time_text
    )
    if times is None:
        raise ValueError("Missing explicit start time")
    # The requested month supplies the year that the publisher omits in its cards.
    begins = local_time(f"{date_text} {month.year} {times[1]}")
    finishes = local_time(f"{date_text} {month.year} {times[2]}") if times[2] else None
    if begins.month != month.month:
        raise ValueError("Calendar ignored its month filter")
    tags = [
        name
        for name in ("education", "volunteer", "community")
        if card.select_one(f".circle.{name}") is not None
    ]
    content = EventContent(
        title=text(card, "h3"),
        description=text(card, "p"),
        starts_at=begins,
        ends_at=finishes,
        venue=venue,
        location_kind=location_kind(venue),
        tags=tags,
        source_url=HttpUrl(url),
    )
    return ParsedEvent(identity[1], content)


async def collect_trees(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Fetch the calendar's visible month filters; each returns that month's full list."""
    result = SourceCollection()
    for month in months(window_start, window_end):
        feed_url = f"{TREES_URL}?month={month:%Y%m}"
        document = BeautifulSoup(await fetch_bytes(client, feed_url), "html.parser")
        cards = document.select(".event")
        result.records_seen += len(cards)
        if document.select_one("#paginate") is not None:
            result.issues.append(ParseIssue(None, "Monthly calendar unexpectedly paginated"))
        for card in cards:
            if "online store closes" not in text(card, "h3").casefold():
                with result.issue_if_fails(None):
                    result.events.append(_tree_event(card, month))
    return result


def _south_fork_event(card: Tag) -> ParsedEvent:
    anchor = card.select_one(".eventlist-title-link[href]")
    export = card.select_one(".eventlist-meta-export-google[href]")
    if anchor is None or export is None:
        raise ValueError("Missing event link or dated calendar export")
    url = urljoin(SOUTH_FORK_URL, str(anchor["href"]))
    if urlsplit(url).hostname != "southforkconservancy.org":
        raise ValueError("Unexpected event URL")
    # The card's Google Calendar link carries the UTC times; nothing else is fetched.
    query = parse_qs(urlsplit(str(export["href"])).query)
    dates = query.get("dates", [""])[0].split("/")
    if len(dates) != 2:
        raise ValueError("Missing event dates")
    begins, finishes = (
        datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) for value in dates
    )
    venue = query.get("location", [None])[0]
    address = card.select_one(".eventlist-meta-address")
    if address is not None:
        label = " ".join(
            str(string).strip() for string in address.find_all(string=True, recursive=False)
        )
        venue = ", ".join(part for part in (label.strip(), venue) if part)
    content = EventContent(
        title=text(anchor),
        description=text(card, ".eventlist-excerpt"),
        starts_at=begins,
        ends_at=finishes if finishes > begins else None,
        venue=venue,
        location_kind=location_kind(venue),
        tags=["outdoors", "community"],
        source_url=HttpUrl(url),
    )
    return ParsedEvent(urlsplit(url).path, content)


async def collect_south_fork(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    document = BeautifulSoup(await fetch_bytes(client, SOUTH_FORK_URL), "html.parser")
    cards = document.select("article.eventlist-event")
    result = SourceCollection(records_seen=len(cards))
    for card in cards:
        with result.issue_if_fails(None):
            result.events.append(_south_fork_event(card))
    return result
