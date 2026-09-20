"""Read TAG's public GrowthZone calendar, event details, and iCalendar exports."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from icalendar import Calendar
from pydantic import HttpUrl, JsonValue, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import publisher_url
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection, in_window
from app.storage.models import EventContent

LISTING_URL = "https://members.tagonline.org/calendar"


@dataclass(frozen=True)
class _Listing:
    external_id: str
    url: str


def _text(value: str | Tag | None) -> str:
    if isinstance(value, str):
        value = BeautifulSoup(value, "html.parser")
    return " ".join(value.get_text(" ", strip=True).split()) if value else ""


def _internal_url(value: str, prefix: str) -> str:
    return publisher_url(value, LISTING_URL, prefix)


def _listing(html: bytes) -> tuple[list[_Listing], list[ParseIssue]]:
    soup = BeautifulSoup(html, "html.parser")
    summary = _text(soup.select_one(".gz-subtitle"))
    count = re.fullmatch(r"Results:\s*([\d,]+)", summary)
    if count is None:
        raise ValueError("TAG did not publish the expected calendar result count")
    cards = soup.select(".gz-events-card")
    issues: list[ParseIssue] = []
    if int(count[1].replace(",", "")) != len(cards):
        issues.append(ParseIssue(None, "TAG calendar result count exceeds the retrieved listing"))
    listings: list[_Listing] = []
    for card in cards:
        external_id: str | None = None
        try:
            link = card.select_one('[itemprop="name"] a[href]')
            href = link.get("href") if link else None
            if not isinstance(href, str):
                raise ValueError("Missing event detail link")
            url = _internal_url(href, "/calendar/Details/")
            match = re.search(r"-(\d+)$", urlsplit(url).path)
            if match is None:
                raise ValueError("Missing stable GrowthZone event identity")
            external_id = match.group(1)
            if not external_id:
                raise ValueError("Missing stable GrowthZone event identity")
            listings.append(_Listing(external_id, url))
        except ValueError as exc:
            issues.append(ParseIssue(external_id, str(exc)))
    return listings, issues


def _detail(html: bytes) -> tuple[dict[str, JsonValue], str, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[dict[str, JsonValue]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        item = json.loads(script.get_text())
        if isinstance(item, dict) and item.get("@type") == "Event":
            events.append(item)
    if len(events) != 1:
        raise ValueError("Expected exactly one Event in the TAG detail page")
    ical_url = next(
        (
            _internal_url(href, "/calendar/ICal/")
            for link in soup.select("a[href]")
            if isinstance(href := link.get("href"), str) and "/calendar/ICal/" in href
        ),
        None,
    )
    if ical_url is None:
        raise ValueError("Event detail is missing its public iCalendar export")
    register = soup.select_one("a.gz-btn-register[href]")
    registration = register.get("href") if register else None
    return events[0], ical_url, registration if isinstance(registration, str) else None


def _parse(
    listing: _Listing,
    data: dict[str, JsonValue],
    ical: bytes,
    registration: str | None,
) -> ParsedEvent | None:
    # GrowthZone publishes P1H instead of PT1H for this calendar cache hint.
    # Ignore that invalid non-event metadata; keep the original export in raw_payload.
    calendar_data = re.sub(rb"(?m)^REFRESH-INTERVAL:P1H\r?\n", b"", ical)
    components = Calendar.from_ical(calendar_data).walk("VEVENT")
    if len(components) != 1:
        raise ValueError("Expected one event in the per-event iCalendar export")
    event = components[0]
    if event.get("RRULE") is not None or event.get("RECURRENCE-ID") is not None:
        raise ValueError("Unexpected recurrence in a TAG occurrence export")
    start = event.decoded("DTSTART")
    end = event.decoded("DTEND") if "DTEND" in event else None
    if not isinstance(start, date) or (end is not None and not isinstance(end, date)):
        raise ValueError("Invalid calendar dates")
    title = _text(str(data.get("name") or event.get("SUMMARY") or ""))
    description = _text(str(data.get("description") or event.get("DESCRIPTION") or ""))
    location = data.get("location")
    address = location.get("address") if isinstance(location, dict) else None
    city = str(address.get("addressLocality") or "") if isinstance(address, dict) else ""
    state = str(address.get("addressRegion") or "") if isinstance(address, dict) else ""
    venue_name = str(location.get("name") or "") if isinstance(location, dict) else ""
    venue_address = str(event.get("LOCATION") or "").strip()
    venue = ", ".join(part for part in (venue_name, venue_address) if part)
    kind: Literal["in_person", "online", "hybrid", "unknown"] = "unknown"
    if re.search(r"\bhybrid\b", venue, re.I):
        kind = "hybrid"
    elif re.search(r"\b(?:virtual|online|zoom)\b", venue, re.I):
        kind = "online"
    elif venue and venue_name.casefold() not in {"tbd", "tba", "to be determined"}:
        kind = "in_person"
    # TAG operates statewide. Keep virtual access, exclude explicitly remote venues.
    if kind != "online" and (
        (state and state.casefold() not in {"ga", "georgia"})
        or city.casefold() in {"athens", "augusta", "columbus", "macon", "savannah", "valdosta"}
    ):
        return None
    audience = ["TAG members"] if re.search(r"\bmembers?[- ]only\b", title, re.I) else []
    price_status: Literal["free", "paid", "conditional", "unknown"] = "unknown"
    price_details = None
    if match := re.search(
        r"\bfree(?:[,\s]+(?:statewide|virtual|online)){0,2}\s+event\b|\bfree admission\b",
        description,
        re.I,
    ):
        price_status, price_details = "free", match[0]
    elif match := re.search(
        r"\b(?:tickets?|admission|registration)\s*(?:is |are |: )?\$\s*\d+(?:\.\d{2})?",
        description,
        re.I,
    ):
        price_status, price_details = "paid", match[0]
    cancelled = (
        str(event.get("STATUS", "")).upper() == "CANCELLED"
        or data.get("eventStatus") == "https://schema.org/EventCancelled"
        or re.match(r"^[\s*\[\(]*(?:cancelled|canceled)\b", title, re.I) is not None
    )
    all_day = not isinstance(start, datetime) or (
        str(event.get("X-MICROSOFT-CDO-ALLDAYEVENT", "")).upper() == "TRUE"
    )
    starts_at = ends_at = None
    start_date = end_date = None
    if all_day:
        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else end
    else:
        if not isinstance(start, datetime) or start.tzinfo is None:
            raise ValueError("Timed event export must specify a timezone")
        starts_at = start.astimezone(UTC)
        if end is not None:
            if not isinstance(end, datetime) or end.tzinfo is None:
                raise ValueError("Timed event end must specify a timezone")
            ends_at = end.astimezone(UTC) if end != start else None
    return ParsedEvent(
        external_id=listing.external_id,
        content=EventContent(
            title=title,
            description=description,
            all_day=all_day,
            starts_at=starts_at,
            ends_at=ends_at,
            start_date=start_date,
            end_date=end_date,
            timezone="America/New_York",
            venue=venue or None,
            location_kind=kind,
            region="atlanta",
            audience=audience,
            price_status=price_status,
            price_details=price_details,
            status="cancelled" if cancelled else "scheduled",
            source_url=HttpUrl(listing.url),
            registration_url=HttpUrl(registration) if registration else None,
        ),
        # Export DTSTAMP is generated at download, not an event revision.
        source_updated_at=None,
        raw_payload={
            "growthzone_id": listing.external_id,
            "event": data,
            "icalendar": ical.decode("utf-8-sig"),
            "registration_url": registration,
            "feed_urls": [LISTING_URL],
        },
    )


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    result = SourceCollection(events=[], records_seen=0, requests=1, issues=[])
    # These are the public search form's GET fields, not an authenticated API.
    params = {
        "DateFilter": "5",
        "from": (window_start - timedelta(days=1)).strftime("%m/%d/%Y"),
        "to": window_end.strftime("%m/%d/%Y"),
        "mode": "0",
    }
    try:
        listings, issues = _listing(
            await fetch_bytes(client, LISTING_URL + "/Search", params=params)
        )
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        result.issues.append(ParseIssue(None, f"TAG listing failed ({type(exc).__name__})"))
        return result
    result.records_seen = len(listings)
    result.issues.extend(issues)
    semaphore = asyncio.Semaphore(2)

    async def fetch(url: str) -> bytes:
        async with semaphore:
            await asyncio.sleep(0.5)
            result.requests += 1
            return await fetch_bytes(client, url)

    async def detail(listing: _Listing) -> ParsedEvent | ParseIssue | None:
        try:
            data, ical_url, registration = _detail(await fetch(listing.url))
            if not urlsplit(ical_url).path.endswith(f"-{listing.external_id}.ics"):
                raise ValueError("The iCalendar export does not match the listed event")
            return _parse(listing, data, await fetch(ical_url), registration)
        except (
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            ValidationError,
        ) as exc:
            return ParseIssue(
                listing.external_id, f"TAG detail failed ({type(exc).__name__}: {exc})"
            )

    for parsed in await asyncio.gather(*(detail(item) for item in listings)):
        if isinstance(parsed, ParseIssue):
            result.issues.append(parsed)
        elif parsed is not None and in_window(parsed.content, window_start, window_end):
            result.events.append(parsed)
    return result
