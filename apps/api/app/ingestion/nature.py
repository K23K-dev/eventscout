"""Public HTML calendars for local conservation and volunteering events."""

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import CANCELLED_TITLE, localize, location_kind
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

EASTERN = ZoneInfo("America/New_York")
TREES_URL = "https://www.treesatlanta.org/get-involved/events/"
SOUTH_FORK_URL = "https://southforkconservancy.org/events"


def _text(parent: Tag, selector: str) -> str:
    element = parent.select_one(selector)
    if element is None:
        raise ValueError(f"Missing {selector}")
    return element.get_text(" ", strip=True)


def _local_time(year: int, date_text: str, clock: str) -> datetime:
    # The requested month supplies the year that the publisher omits in its cards.
    normalized = clock.strip().upper()
    pattern = "%Y %a %b %d %I:%M%p" if ":" in normalized else "%Y %a %b %d %I%p"
    return localize(datetime.strptime(f"{year} {date_text} {normalized}", pattern), EASTERN)


def _tree_event(card: Tag, year: int, month: int) -> ParsedEvent:
    anchor = card.find_parent("a", href=True)
    if anchor is None:
        raise ValueError("Missing event URL")
    url = urljoin(TREES_URL, str(anchor["href"]))
    identity = re.search(r"-([a-zA-Z0-9]{18})/?$", urlsplit(url).path)
    if urlsplit(url).hostname != "www.treesatlanta.org" or identity is None:
        raise ValueError("Unrecognized Trees Atlanta event identity")
    parts = _text(card, ".time-place").split("|")
    if len(parts) not in (2, 3):
        raise ValueError("Unrecognized event date and venue")
    date_text, time_text = (part.strip() for part in parts[:2])
    venue = parts[2].strip() if len(parts) == 3 else None
    times = re.fullmatch(
        r"(\d{1,2}(?::\d{2})?[ap]m)(?:\s*-\s*(\d{1,2}(?::\d{2})?[ap]m))?", time_text
    )
    if times is None:
        raise ValueError("Missing explicit start time")
    begins = _local_time(year, date_text, times[1])
    finishes = _local_time(year, date_text, times[2]) if times[2] else None
    if begins.month != month:
        raise ValueError("Calendar ignored its month filter")
    tags = [
        name
        for name in ("education", "volunteer", "community")
        if card.select_one(f".circle.{name}") is not None
    ]
    title = _text(card, "h3")
    content = EventContent(
        title=title,
        description=_text(card, "p"),
        starts_at=begins,
        ends_at=finishes,
        venue=venue,
        region="atlanta",
        location_kind=location_kind(venue),
        tags=tags,
        source_url=HttpUrl(url),
        status="cancelled" if CANCELLED_TITLE.match(title) else "scheduled",
    )
    return ParsedEvent(
        identity[1],
        content,
        None,
        {
            "date_label": date_text,
            "time_label": time_text,
            "description_is_excerpt": True,
        },
    )


async def collect_trees(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Fetch the calendar's visible month filters; each returns that month's full list."""
    result = SourceCollection([], 0, [])
    current = window_start.astimezone(EASTERN).replace(day=1)
    last = window_end.astimezone(EASTERN)
    while (current.year, current.month) <= (last.year, last.month):
        feed_url = f"{TREES_URL}?month={current:%Y%m}"
        document = BeautifulSoup(await fetch_bytes(client, feed_url), "html.parser")
        if document.select_one("#events-filters") is None:
            raise ValueError("Unrecognized Trees Atlanta calendar")
        cards = document.select(".event")
        result.records_seen += len(cards)
        if document.select_one("#paginate") is not None:
            result.issues.append(ParseIssue(None, "Monthly calendar unexpectedly paginated"))
        for card in cards:
            try:
                if "online store closes" in _text(card, "h3").casefold():
                    continue
                event = _tree_event(card, current.year, current.month)
                result.events.append(event)
            except ValueError as exc:
                result.issues.append(ParseIssue(None, str(exc)))
        current = current.replace(
            year=current.year + (current.month == 12), month=current.month % 12 + 1
        )
    return result


def _south_fork_event(card: Tag) -> ParsedEvent:
    anchor = card.select_one(".eventlist-title-link[href]")
    export = card.select_one(".eventlist-meta-export-google[href]")
    if anchor is None or export is None:
        raise ValueError("Missing event link or dated calendar export")
    url = urljoin(SOUTH_FORK_URL, str(anchor["href"]))
    if urlsplit(url).hostname != "southforkconservancy.org":
        raise ValueError("Unexpected event URL")
    # The visible Google Calendar link carries UTC times. Read its data only;
    # there is no need to visit Google or the robot-disallowed ICS endpoint.
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
            str(text).strip() for text in address.find_all(string=True, recursive=False)
        )
        venue = ", ".join(part for part in (label.strip(), venue) if part)
    title = anchor.get_text(" ", strip=True)
    content = EventContent(
        title=title,
        description=(
            _text(card, ".eventlist-excerpt") if card.select_one(".eventlist-excerpt") else ""
        ),
        starts_at=begins,
        ends_at=finishes if finishes > begins else None,
        venue=venue,
        region="atlanta",
        location_kind=location_kind(venue),
        tags=["outdoors", "community"],
        source_url=HttpUrl(url),
        status="cancelled" if CANCELLED_TITLE.match(title) else "scheduled",
    )
    return ParsedEvent(
        urlsplit(url).path,
        content,
        None,
        {"calendar_dates": query["dates"][0]},
    )


async def collect_south_fork(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    document = BeautifulSoup(await fetch_bytes(client, SOUTH_FORK_URL), "html.parser")
    cards = document.select("article.eventlist-event")
    if not cards and document.select_one(".eventlist") is None:
        raise ValueError("Unrecognized South Fork Conservancy calendar")
    result = SourceCollection([], len(cards), [])
    for card in cards:
        try:
            event = _south_fork_event(card)
            result.events.append(event)
        except ValueError as exc:
            result.issues.append(ParseIssue(None, str(exc)))
    return result
