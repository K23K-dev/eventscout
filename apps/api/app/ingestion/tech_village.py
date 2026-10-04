"""Read Atlanta Tech Village's public Webflow event listing and detail pages."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import unescape
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from dateutil import parser
from pydantic import HttpUrl, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    MEMBERS_ONLY,
    described_price,
    http_url,
    localize,
    location_kind,
)
from app.ingestion.parsing import text as _text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

LISTING_URL = "https://atlantatechvillage.com/events"
_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class _Listing:
    external_id: str
    url: str
    day: date


def _url(value: str, *, internal: bool = False) -> str:
    result = urljoin(LISTING_URL, unescape(value))
    parts = urlsplit(result)
    if internal and (
        parts.hostname != "atlantatechvillage.com" or not parts.path.startswith("/events")
    ):
        raise ValueError("Expected an Atlanta Tech Village event link")
    return str(http_url(result))


def _listing(html: bytes) -> tuple[list[_Listing], list[ParseIssue], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".single-item")
    events: list[_Listing] = []
    issues: list[ParseIssue] = []
    for card in cards:
        external_id: str | None = None
        try:
            link = card.select_one("a[href][data-wf-cms-context]")
            if link is None:
                raise ValueError("Missing stable Webflow item identity")
            identities = {
                str(item["itemId"])
                for item in json.loads(unquote(str(link["data-wf-cms-context"])))
                if isinstance(item, dict) and "itemId" in item
            }
            if len(identities) != 1:
                raise ValueError("Ambiguous Webflow item identity")
            external_id = identities.pop()
            if not re.fullmatch(r"[a-f0-9]{24}", external_id):
                raise ValueError("Invalid Webflow item identity")
            day = datetime.strptime(_text(card.select_one(".event-date-2")), "%B %d, %Y").date()
            events.append(_Listing(external_id, _url(str(link["href"]), internal=True), day))
        except (ValueError, TypeError, KeyError) as exc:
            issues.append(ParseIssue(external_id, str(exc)))
    next_link = soup.select_one("a.w-pagination-next[href]")
    return events, issues, _url(str(next_link["href"]), internal=True) if next_link else None


def _local_time(day: str, clock: str) -> datetime:
    return localize(parser.parse(f"{day} {clock}"), _TIMEZONE).astimezone(UTC)


def _parse_detail(html: bytes, listing: _Listing) -> ParsedEvent:
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.select_one("h1.event-title"))
    fields = {
        _text(group.select_one(".detail-heading")).casefold(): group
        for group in soup.select(".event-details-group")
    }
    when = fields.get("when")
    if not title or when is None:
        raise ValueError("Event title or WHEN section is missing")
    day = _text(when.select_one(".text-weight-light"))
    clocks = re.findall(r"\b\d{1,2}:\d{2}\s*[AP]M\b", _text(when.select_one(".time-group")))
    if len(clocks) not in {1, 2}:
        raise ValueError("Expected one or two explicit event times")
    starts_at = _local_time(day, clocks[0])
    ends_at = _local_time(day, clocks[1]) if len(clocks) == 2 else None
    if ends_at == starts_at:
        ends_at = None
    where = fields.get("where")
    venue_node = where.select_one(".w-richtext") if where else None
    venue = _text(venue_node)
    description_node = soup.select_one(".text-rich-text.w-richtext")
    description = _text(description_node)
    registration = next(
        (
            _url(href)
            for link in soup.select(".event-links a[href]")
            if _text(link).casefold() in {"rsvp here", "register", "register here"}
            and isinstance(href := link.get("href"), str)
        ),
        None,
    )
    audience = ["Atlanta Tech Village members"] if MEMBERS_ONLY.search(title) else []
    price_status, price_details = described_price(description)
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts_at,
        ends_at=ends_at,
        venue=venue or None,
        location_kind=location_kind(venue),
        audience=audience,
        price_status=price_status,
        price_details=price_details,
        source_url=HttpUrl(listing.url),
        registration_url=HttpUrl(registration) if registration else None,
    )
    return ParsedEvent(
        external_id=listing.external_id,
        content=content,
        source_updated_at=None,
        raw_payload={
            "cms_item_id": listing.external_id,
            "url": listing.url,
            "listing_date": str(listing.day),
            "when": _text(when),
            "venue_html": str(venue_node) if venue_node else None,
            "description_html": str(description_node) if description_node else None,
            "registration_url": registration,
        },
    )


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    result = SourceCollection()
    listings: dict[str, _Listing] = {}
    url: str | None = LISTING_URL
    seen: set[str] = set()
    # Follow only pagination explicitly offered by the public listing.
    while url is not None:
        if url in seen or len(seen) >= 10:
            result.issues.append(ParseIssue(None, "Event listing pagination did not terminate"))
            break
        seen.add(url)
        try:
            page, issues, next_url = _listing(await fetch_bytes(client, url))
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(ParseIssue(None, f"ATV listing failed ({type(exc).__name__})"))
            break
        result.issues.extend(issues)
        for item in page:
            listings[item.external_id] = item
        url = next_url
        if url is not None:
            await asyncio.sleep(0.5)
    result.records_seen = len(listings)
    start_day = window_start.astimezone(_TIMEZONE).date() - timedelta(days=1)
    end_day = window_end.astimezone(_TIMEZONE).date()
    candidates = [item for item in listings.values() if start_day <= item.day <= end_day]
    semaphore = asyncio.Semaphore(2)

    async def detail(item: _Listing) -> ParsedEvent | ParseIssue:
        try:
            async with semaphore:
                await asyncio.sleep(0.5)
                html = await fetch_bytes(client, item.url)
            return _parse_detail(html, item)
        except ValidationError:
            return ParseIssue(item.external_id, "Invalid event fields on ATV detail page")
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            return ParseIssue(item.external_id, f"ATV detail failed ({type(exc).__name__}: {exc})")

    for parsed in await asyncio.gather(*(detail(item) for item in candidates)):
        if isinstance(parsed, ParseIssue):
            result.issues.append(parsed)
        else:
            result.events.append(parsed)
    return result
