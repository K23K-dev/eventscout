"""Read public ChamberMaster month grids and their linked event detail pages."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes, fetch_pages
from app.ingestion.parsing import MEMBERS_ONLY, cost_price, location_kind, months, text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

_ADMINISTRATIVE = re.compile(
    r"\b(?:chamber|office) closed\b|\bstaff meeting\b|^\W*specials\W|"
    r"\b(?:sponsorship opportunities|sponsorship registration|advertising opportunities)\b",
    re.I,
)


@dataclass(frozen=True)
class _Listing:
    external_id: str
    url: str
    title: str


def _detail_url(href: str, base: str) -> str:
    parts = urlsplit(urljoin(base, href))
    on_calendar = parts.scheme == "https" and parts.netloc == urlsplit(base).netloc
    if not on_calendar or not parts.path.startswith("/events/details/"):
        raise ValueError("Expected an event page on the chamber's own calendar")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _listing(html: bytes, base: str, result: SourceCollection) -> list[_Listing]:
    soup = BeautifulSoup(html, "html.parser")
    items: dict[str, _Listing] = {}
    for link in soup.select('table.gz-cal-grid a[href*="/events/details/"]'):
        with result.issue_if_fails(None):
            url = _detail_url(str(link.get("href")), base)
            identity = re.search(r"-(\d+)$", urlsplit(url).path)
            if identity is None:
                raise ValueError("Event is missing its stable ChamberMaster identity")
            items[identity[1]] = _Listing(identity[1], url, text(link))
    return list(items.values())


def _content(node: Tag, selector: str) -> str | None:
    field = node.select_one(selector)
    value = field.get("content") if field else None
    return value if isinstance(value, str) else None


def _parse(html: bytes, item: _Listing, publisher: str) -> ParsedEvent | None:
    event = BeautifulSoup(html, "html.parser").select_one(
        '.gz-event-details[itemtype="http://schema.org/Event"]'
    )
    if event is None:
        raise ValueError("Public event detail structure is missing")
    title = _content(event, 'meta[itemprop="name"]') or item.title
    if _ADMINISTRATIVE.search(title):
        return None
    # Without a clock time the structured start is only a date, and the event can't be timed.
    if not re.search(r"\d{1,2}:\d{2}\s*[AP]M", text(event, ".gz-details-time"), re.I):
        raise ValueError("Event has a date but no clock time")
    starts_at = datetime.fromisoformat(str(_content(event, '[itemprop="startDate"]')))
    end = _content(event, '[itemprop="endDate"]')
    ends_at = datetime.fromisoformat(end) if end else None
    if ends_at == starts_at:
        ends_at = None
    description = text(event, ".gz-event-description")
    venue = text(event, '[itemprop="location"] [itemprop="name"]')
    kind = location_kind(venue)
    if kind == "in_person" and re.search(
        r"\bWashington\s*,?\s+D\.?\s*C\.?\b|\bDistrict of Columbia\b", venue, re.I
    ):
        return None
    fees = text(event, ".gz-event-fees .gz-event-fees")
    price, price_details = cost_price(fees)
    audience = []
    if MEMBERS_ONLY.search(f"{title} {description} {fees}"):
        audience.append(f"{publisher} members")
    if re.search(r"\bboard of directors\b|\bexecutive committee\b", title, re.I):
        audience.append("Board or executive committee members")
    elif re.search(r"\bcommittee meeting\b", title, re.I):
        audience.append("Committee members")
    registration = next(
        (
            href
            for link in event.select('a[href*="/events/register/"]')
            if isinstance(href := link.get("href"), str)
        ),
        None,
    )
    status = _content(event, 'meta[itemprop="eventStatus"]') or ""
    cancelled = status in {
        "EventCancelled",
        "https://schema.org/EventCancelled",
        "http://schema.org/EventCancelled",
    }
    return ParsedEvent(
        item.external_id,
        EventContent(
            title=title,
            description=description,
            starts_at=starts_at,
            ends_at=ends_at,
            venue=venue or None,
            location_kind=kind,
            audience=audience,
            price_status=price,
            price_details=price_details,
            status="cancelled" if cancelled else "scheduled",
            source_url=HttpUrl(item.url),
            registration_url=HttpUrl(registration) if registration else None,
        ),
    )


async def collect(
    client: httpx.AsyncClient,
    *,
    calendar_url: str,
    publisher: str,
    window_start: datetime,
    window_end: datetime,
) -> SourceCollection:
    result = SourceCollection()
    listings: dict[str, _Listing] = {}
    for index, month in enumerate(months(window_start, window_end)):
        if index:
            await asyncio.sleep(0.5)
        with result.issue_if_fails(f"{month:%B %Y} grid"):
            grid = await fetch_bytes(client, f"{calendar_url.rstrip('/')}/{month.isoformat()}")
            for item in _listing(grid, calendar_url, result):
                listings[item.external_id] = item
    result.records_seen = len(listings)
    if len(listings) > 1000:
        result.issues.append(
            ParseIssue(None, "Month grids exceeded the 1,000-occurrence read limit")
        )
        return result
    items = [item for item in listings.values() if not _ADMINISTRATIVE.search(item.title)]
    pages = await fetch_pages(client, [item.url for item in items])
    for item, page in zip(items, pages, strict=True):
        with result.issue_if_fails(item.external_id):
            if isinstance(page, BaseException):
                raise page
            if event := _parse(page, item, publisher):
                result.events.append(event)
    return result
