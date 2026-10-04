"""Read the GT Library's paginated public calendar and stable Drupal event pages."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from dateutil import parser
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    CANCELLED_TITLE,
    described_price,
    http_url,
    issue_message,
    localize,
    location_kind,
    text,
)
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

LISTING_URL = "https://library.gatech.edu/events-workshops"
_TIMEZONE = ZoneInfo("America/New_York")
_MAX_PAGES = 50


def _text(parent: Tag | BeautifulSoup, selector: str) -> str:
    matches = parent.select(selector)
    if len(matches) > 1:
        raise ValueError(f"Expected one {selector} field")
    return " ".join(matches[0].get_text(" ", strip=True).split()) if matches else ""


def _url(value: str, *, library_only: bool = False) -> str:
    absolute = urljoin(LISTING_URL, value)
    url = http_url(absolute)
    if library_only and urlsplit(absolute).hostname != "library.gatech.edu":
        raise ValueError("Library calendar link unexpectedly leaves its publisher")
    return str(url)


def _href(tag: Tag | None) -> str:
    if tag is None or not isinstance(value := tag.get("href"), str) or not value:
        raise ValueError("Missing event link")
    return value


def _local_time(day: str, clock: str) -> datetime:
    return localize(parser.parse(f"{day} {clock}"), _TIMEZONE).astimezone(UTC)


def _parse_detail(html: bytes, url: str, listing_venue: str) -> ParsedEvent:
    soup = BeautifulSoup(html, "html.parser")
    shortlink = _url(_href(soup.select_one('link[rel="shortlink"]')), library_only=True)
    match = re.fullmatch(r"/node/(\d+)", urlsplit(shortlink).path)
    if match is None:
        raise ValueError("Missing stable Drupal node ID")
    node_id = match.group(1)
    detail = soup.select_one("main .node-detail")
    if detail is None:
        raise ValueError("Missing Library event details")
    title = _text(detail, "h1")
    day = " ".join(_text(detail, f".node-detail__{part}") for part in ("day", "month", "year"))
    clock = _text(detail, ".hours-of-operation")
    # Library pages publish campus-local wall times, without a UTC offset.
    all_day = bool(re.fullmatch(r"all[ -]day", clock, re.I))
    start_date = parser.parse(day).date() if all_day else None
    starts_at = ends_at = None
    if not all_day:
        clocks = re.split(r"\s*[-–—]\s*", clock)
        if len(clocks) not in {1, 2}:
            raise ValueError("Expected one start time and an optional end time")
        starts_at = _local_time(day, clocks[0])
        ends_at = _local_time(day, clocks[1]) if len(clocks) == 2 else None
    body = detail.select_one(".node-detail__content-body")
    body_html = str(body) if body else ""
    description = text(body)
    detail_venue = _text(detail, ".taxonomy-box.location")
    venue = listing_venue or detail_venue
    if venue.casefold() == "in person":
        venue = ""
    register = detail.select_one("a.register-button")
    registration_url = _url(_href(register)) if register is not None else None
    category = _text(detail, ".node-detail__category")
    price_status, price_details = described_price(description)
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        start_date=start_date,
        end_date=start_date + timedelta(days=1) if start_date is not None else None,
        timezone="America/New_York",
        venue=venue or None,
        location_kind=location_kind(venue or detail_venue),
        region="gt",
        price_status=price_status,
        price_details=price_details,
        tags=[category] if category else [],
        source_url=HttpUrl(url),
        registration_url=HttpUrl(registration_url) if registration_url else None,
        status="cancelled" if CANCELLED_TITLE.match(title) else "scheduled",
    )
    return ParsedEvent(
        external_id=f"node:{node_id}",
        content=content,
        source_updated_at=None,
        raw_payload={
            "node_id": node_id,
            "shortlink": shortlink,
            "url": url,
            "title": title,
            "date": day,
            "time": clock,
            "timezone": "America/New_York",
            "listing_venue": listing_venue,
            "detail_venue": detail_venue,
            "description_html": body_html,
            "registration_url": registration_url,
            "category": category,
        },
    )


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Collect upcoming Library events with bounded pagination and two detail requests at once.

    Every date comes from the event's published date and time, interpreted in the
    Library's Atlanta timezone. Missing fees and audience restrictions remain unknown.
    Individual failures are returned as issues so a partial import cannot look complete.
    """
    result = SourceCollection(events=[], records_seen=0, issues=[])
    listings: dict[str, str] = {}
    visited: set[str] = set()
    next_url: str | None = LISTING_URL
    while next_url is not None and len(visited) < _MAX_PAGES:
        if next_url in visited:
            result.issues.append(ParseIssue(None, "Library pagination repeats an earlier page"))
            break
        visited.add(next_url)
        try:
            html = await fetch_bytes(client, next_url)
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".public-programming-listing--view a.event-card")
            result.records_seen += len(cards)
            for card in cards:
                url = _url(_href(card), library_only=True)
                if not urlsplit(url).path.startswith("/events/"):
                    raise ValueError("Unexpected Library event detail path")
                listings.setdefault(url, _text(card, ".event-card__class_type"))
            next_links = {
                _url(_href(tag), library_only=True)
                for tag in soup.select('.public-programming-listing--view a[rel="next"]')
            }
            if len(next_links) > 1:
                raise ValueError("Library pagination has conflicting next links")
            next_url = next(iter(next_links), None)
            if next_url and urlsplit(next_url).path != "/events-workshops":
                raise ValueError("Unexpected Library pagination path")
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            result.issues.append(ParseIssue(None, f"Library listing failed: {message}"))
            break
    else:
        if next_url is not None:
            result.issues.append(ParseIssue(None, "Library pagination exceeded the 50-page limit"))

    semaphore = asyncio.Semaphore(2)

    async def detail(url: str, venue: str) -> ParsedEvent | ParseIssue:
        async with semaphore:
            try:
                return _parse_detail(await fetch_bytes(client, url), url, venue)
            except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                return ParseIssue(url, issue_message(exc))

    parsed = await asyncio.gather(*(detail(url, venue) for url, venue in listings.items()))
    identities: set[str] = set()
    for event in parsed:
        if isinstance(event, ParseIssue):
            result.issues.append(event)
        elif event.external_id in identities:
            result.issues.append(ParseIssue(event.external_id, "Repeated Library node ID"))
        else:
            identities.add(event.external_id)
            result.events.append(event)
    result.events.sort(key=lambda event: event.external_id)
    return result
