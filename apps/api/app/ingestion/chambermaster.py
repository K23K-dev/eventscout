"""Read public ChamberMaster month grids and their linked event detail pages."""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from icalendar import Calendar
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    CANCELLED_TITLE,
    MEMBERS_ONLY,
    cost_price,
    location_kind,
    publisher_url,
)
from app.ingestion.parsing import text as _text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

_TIMEZONE = ZoneInfo("America/New_York")
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


def _event_url(base: str, value: str, *, path: str = "/events/details/") -> str:
    return publisher_url(value, base, path)


def _listing(html: bytes, base: str) -> tuple[list[_Listing], list[ParseIssue]]:
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.select_one("table.gz-cal-grid")
    if grid is None:
        raise ValueError("Public month calendar grid is missing")
    items: dict[str, _Listing] = {}
    issues: list[ParseIssue] = []
    for link in grid.select('a[href*="/events/details/"]'):
        try:
            href = link.get("href")
            if not isinstance(href, str):
                raise ValueError("Event link is missing")
            url = _event_url(base, href)
            identity = re.search(r"-(\d+)$", urlsplit(url).path)
            if identity is None:
                raise ValueError("Event is missing its stable ChamberMaster identity")
            items[identity[1]] = _Listing(identity[1], url, _text(link))
        except ValueError as exc:
            issues.append(ParseIssue(None, str(exc)))
    return list(items.values()), issues


def _datetime(value: str | None) -> datetime:
    if not value:
        raise ValueError("Event is missing a structured timestamp")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Structured event timestamp must specify a timezone")
    return result.astimezone(UTC)


def _content(node: Tag, selector: str) -> str | None:
    field = node.select_one(selector)
    value = field.get("content") if field else None
    return value if isinstance(value, str) else None


def _detail(html: bytes) -> tuple[Tag, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    event = soup.select_one('.gz-event-details[itemtype="http://schema.org/Event"]')
    if event is None:
        raise ValueError("Public event detail structure is missing")
    # Date-only activities need the advertised export's VALUE=DATE semantics.
    time_label = _text(event.select_one(".gz-details-time"))
    ical = None
    if not re.search(r"\d{1,2}:\d{2}\s*[AP]M", time_label, re.I):
        for link in soup.select('a[href*="/events/addtocalendar/"]'):
            href = link.get("href")
            if isinstance(href, str) and "format=ICal" in href:
                ical = href
                break
        if ical is None:
            raise ValueError("Event without an explicit clock time needs its iCalendar export")
    return event, ical


def _parse(
    item: _Listing, event: Tag, *, calendar_url: str, publisher: str, ical: bytes | None
) -> ParsedEvent | None:
    title = _content(event, 'meta[itemprop="name"]') or item.title
    if _ADMINISTRATIVE.search(title):
        return None
    raw_end = _content(event, '[itemprop="endDate"]')
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    if ical is None:
        starts_at = _datetime(_content(event, '[itemprop="startDate"]'))
        ends_at = _datetime(raw_end) if raw_end else None
    if ends_at == starts_at:
        ends_at = None
    all_day = False
    start_date = end_date = None
    calendar_cancelled = False
    if ical is not None:
        events = Calendar.from_ical(ical).walk("VEVENT")
        if len(events) != 1:
            raise ValueError("Expected one occurrence in the event iCalendar export")
        component = events[0]
        calendar_cancelled = str(component.get("STATUS", "")).upper() == "CANCELLED"
        if component.get("RRULE") is not None:
            raise ValueError("Unexpected recurrence rule in an occurrence export")
        start = component.decoded("DTSTART")
        end = component.decoded("DTEND") if "DTEND" in component else None
        if isinstance(start, date) and not isinstance(start, datetime):
            all_day = True
            starts_at = ends_at = None
            start_date = start
            if end is not None and (not isinstance(end, date) or isinstance(end, datetime)):
                raise ValueError("Date-only event end has an incompatible type")
            end_date = end
        elif isinstance(start, datetime) and start.tzinfo is not None:
            starts_at = start.astimezone(UTC)
            if end is not None:
                if not isinstance(end, datetime) or end.tzinfo is None:
                    raise ValueError("Timed event end must specify a timezone")
                ends_at = end.astimezone(UTC) if end != start else None
        else:
            raise ValueError("Invalid date in event export")
    description_node = event.select_one(".gz-event-description")
    description = _text(description_node)
    venue = _text(event.select_one('[itemprop="location"] [itemprop="name"]'))
    kind = location_kind(venue)
    if kind == "in_person" and re.search(
        r"\bWashington\s*,?\s+D\.?\s*C\.?\b|\bDistrict of Columbia\b", venue, re.I
    ):
        return None
    fees = _text(event.select_one(".gz-event-fees .gz-event-fees"))
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
    cancelled = (
        calendar_cancelled
        or status
        in {
            "EventCancelled",
            "https://schema.org/EventCancelled",
            "http://schema.org/EventCancelled",
        }
        or CANCELLED_TITLE.match(title)
    )
    return ParsedEvent(
        external_id=item.external_id,
        content=EventContent(
            title=title,
            description=description,
            starts_at=starts_at,
            ends_at=ends_at,
            start_date=start_date,
            end_date=end_date,
            all_day=all_day,
            timezone=_TIMEZONE.key,
            venue=venue or None,
            location_kind=kind,
            region="atlanta",
            audience=audience,
            price_status=price,
            price_details=price_details,
            status="cancelled" if cancelled else "scheduled",
            source_url=HttpUrl(item.url),
            registration_url=HttpUrl(registration) if registration else None,
        ),
        source_updated_at=None,
        raw_payload={
            "chambermaster_id": item.external_id,
            "title": title,
            "start": _content(event, '[itemprop="startDate"]'),
            "end": raw_end,
            "venue": venue,
            "fees": fees,
            "description_html": str(description_node) if description_node else None,
            "status": status,
            "registration_url": registration,
            "icalendar": ical.decode("utf-8-sig") if ical else None,
            "feed_urls": [calendar_url],
        },
    )


async def collect(
    client: httpx.AsyncClient,
    *,
    calendar_url: str,
    publisher: str,
    window_start: datetime,
    window_end: datetime,
) -> SourceCollection:
    result = SourceCollection(events=[], records_seen=0, requests=0, issues=[])
    listings: dict[str, _Listing] = {}
    month = window_start.astimezone(_TIMEZONE).date().replace(day=1)
    last = window_end.astimezone(_TIMEZONE).date().replace(day=1)
    if (last.year - month.year) * 12 + last.month - month.month > 12:
        raise ValueError("Calendar window is limited to twelve months")
    while month <= last:
        url = f"{calendar_url.rstrip('/')}/{month.isoformat()}"
        result.requests += 1
        try:
            items, issues = _listing(await fetch_bytes(client, url), calendar_url)
            result.issues.extend(issues)
            for item in items:
                listings[item.external_id] = item
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(ParseIssue(None, f"Month {month} failed ({type(exc).__name__})"))
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        if month <= last:
            await asyncio.sleep(0.5)
    result.records_seen = len(listings)
    if len(listings) > 1000:
        result.issues.append(
            ParseIssue(None, "Month grids exceeded the 1,000-occurrence read limit")
        )
        return result
    semaphore = asyncio.Semaphore(2)

    async def fetch(url: str) -> bytes:
        async with semaphore:
            await asyncio.sleep(0.5)
            result.requests += 1
            return await fetch_bytes(client, url)

    async def detail(item: _Listing) -> ParsedEvent | ParseIssue | None:
        if _ADMINISTRATIVE.search(item.title):
            return None
        try:
            event, ical_url = _detail(await fetch(item.url))
            ical = None
            if ical_url:
                allowed = _event_url(calendar_url, ical_url, path="/events/addtocalendar/")
                if not urlsplit(allowed).path.endswith(f"-{item.external_id}"):
                    raise ValueError("Calendar export does not match the event identity")
                ical = await fetch(allowed + "?format=ICal")
            return _parse(item, event, calendar_url=calendar_url, publisher=publisher, ical=ical)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            return ParseIssue(
                item.external_id, f"Event detail failed ({type(exc).__name__}: {exc})"
            )

    for parsed in await asyncio.gather(*(detail(item) for item in listings.values())):
        if isinstance(parsed, ParseIssue):
            result.issues.append(parsed)
        elif parsed is not None:
            result.events.append(parsed)
    return result
