"""Read Atlanta Tech Village's public Webflow event listing and detail pages."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import unescape
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes, fetch_pages
from app.ingestion.parsing import (
    MEMBERS_ONLY,
    described_price,
    http_url,
    local_time,
    location_kind,
    text,
)
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import ATLANTA, EventContent

LISTING_URL = "https://atlantatechvillage.com/events"


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


def _listing(html: bytes, result: SourceCollection) -> tuple[list[_Listing], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[_Listing] = []
    for card in soup.select(".single-item"):
        with result.issue_if_fails(None):
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
            day = datetime.strptime(text(card, ".event-date-2"), "%B %d, %Y").date()
            events.append(_Listing(external_id, _url(str(link["href"]), internal=True), day))
    next_link = soup.select_one("a.w-pagination-next[href]")
    return events, _url(str(next_link["href"]), internal=True) if next_link else None


def _parse_detail(html: bytes, listing: _Listing) -> ParsedEvent:
    soup = BeautifulSoup(html, "html.parser")
    title = text(soup, "h1.event-title")
    fields = {
        text(group, ".detail-heading").casefold(): group
        for group in soup.select(".event-details-group")
    }
    when = fields.get("when")
    if not title or when is None:
        raise ValueError("Event title or WHEN section is missing")
    day = text(when, ".text-weight-light")
    clocks = re.findall(r"\b\d{1,2}:\d{2}\s*[AP]M\b", text(when, ".time-group"))
    if len(clocks) not in {1, 2}:
        raise ValueError("Expected one or two explicit event times")
    starts_at = local_time(f"{day} {clocks[0]}")
    ends_at = local_time(f"{day} {clocks[1]}") if len(clocks) == 2 else None
    if ends_at == starts_at:
        ends_at = None
    venue = text(fields.get("where"), ".w-richtext")
    description = text(soup, ".text-rich-text.w-richtext")
    registration = next(
        (
            _url(href)
            for link in soup.select(".event-links a[href]")
            if text(link).casefold() in {"rsvp here", "register", "register here"}
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
    return ParsedEvent(listing.external_id, content)


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
            found, next_url = _listing(await fetch_bytes(client, url), result)
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(ParseIssue(None, f"ATV listing failed ({type(exc).__name__})"))
            break
        for item in found:
            listings[item.external_id] = item
        url = next_url
        if url is not None:
            await asyncio.sleep(0.5)
    result.records_seen = len(listings)
    start_day = window_start.astimezone(ATLANTA).date() - timedelta(days=1)
    end_day = window_end.astimezone(ATLANTA).date()
    candidates = [item for item in listings.values() if start_day <= item.day <= end_day]
    pages = await fetch_pages(client, [item.url for item in candidates])
    for item, page in zip(candidates, pages, strict=True):
        with result.issue_if_fails(item.external_id):
            if isinstance(page, BaseException):
                raise page
            result.events.append(_parse_detail(page, item))
    return result
