"""Read the public CivicEngage event subscriptions of three Atlanta-area cities."""

import asyncio
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from icalendar import Calendar, Component
from pydantic import HttpUrl, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    CANCELLED_TITLE,
    cost_price,
    location_kind,
    registration_link,
)
from app.ingestion.parsing import text as _text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection, in_window
from app.storage.models import EventContent

City = Literal["norcross", "lilburn", "lawrenceville"]
CALENDARS: dict[City, tuple[str, int]] = {
    "norcross": ("https://www.norcrossga.net", 22),
    "lilburn": ("https://www.cityoflilburn.com", 25),
    "lawrenceville": ("https://www.lawrencevillega.org", 22),
}


def _parse(event: Component, base: str) -> ParsedEvent:
    identity = str(event.get("UID", ""))
    if not re.fullmatch(r"\d+", identity):
        raise ValueError("Missing stable CivicEngage event ID")
    if any(event.get(key) is not None for key in ("RRULE", "RDATE", "RECURRENCE-ID")):
        raise ValueError("Expected expanded CivicEngage occurrences")
    start = event.decoded("DTSTART")
    end = event.decoded("DTEND") if event.get("DTEND") is not None else None
    if not isinstance(start, date) or (end is not None and not isinstance(end, date)):
        raise ValueError("Invalid CivicEngage event dates")
    all_day = not isinstance(start, datetime)
    starts_at = ends_at = None
    start_date = end_date = None
    if all_day:
        start_date = start
        if isinstance(end, datetime):
            raise ValueError("All-day event has a timed end")
        # The verified single-day exports use DTSTART == DTEND, unlike RFC 5545.
        # Do not assume the same end convention for an unverified multi-day export.
        if end is not None and end != start:
            raise ValueError("Multi-day date-only city export needs visible date verification")
        end_date = start + timedelta(days=1)
    else:
        if not isinstance(start, datetime) or start.tzinfo is None:
            raise ValueError("Event start is missing its timezone")
        starts_at = start.astimezone(UTC)
        if end is not None:
            if not isinstance(end, datetime) or end.tzinfo is None:
                raise ValueError("Timed event has an invalid end")
            # A missing end in CivicEngage is exported as 23:59. The detail page
            # below determines whether an end time was actually published.
            if end > start and (end.hour, end.minute) != (23, 59):
                ends_at = end.astimezone(UTC)
    title = _text(str(event.get("SUMMARY", "")))
    detail_url = f"{base}/calendar.aspx?EID={identity}"
    description = str(event.get("DESCRIPTION", "")).replace(detail_url, "")
    venue = _text(str(event.get("LOCATION", ""))) or None
    updated = event.decoded("LAST-MODIFIED") if event.get("LAST-MODIFIED") else None
    if isinstance(updated, datetime) and updated.tzinfo is None:
        # CivicEngage uses a local timestamp plus TZID for LAST-MODIFIED, which
        # iCalendar's UTC-only property parser otherwise returns without a zone.
        if event["LAST-MODIFIED"].params.get("TZID") == "America/New_York":
            updated = updated.replace(tzinfo=ZoneInfo("America/New_York"))
    if updated is not None and (not isinstance(updated, datetime) or updated.tzinfo is None):
        raise ValueError("Invalid last-modified timestamp")
    return ParsedEvent(
        external_id=identity,
        content=EventContent(
            title=title,
            description=_text(description),
            all_day=all_day,
            starts_at=starts_at,
            ends_at=ends_at,
            start_date=start_date,
            end_date=end_date,
            timezone="America/New_York",
            venue=venue,
            location_kind="in_person" if venue else "unknown",
            region="atlanta",
            tags=["Community"],
            source_url=HttpUrl(detail_url),
            status="cancelled"
            if str(event.get("STATUS", "")).upper() == "CANCELLED" or CANCELLED_TITLE.match(title)
            else "scheduled",
        ),
        source_updated_at=updated,
        raw_payload={
            "icalendar": event.to_ical().decode("utf-8"),
        },
    )


def _detail(html: bytes, event: ParsedEvent) -> ParsedEvent:
    soup = BeautifulSoup(html, "html.parser")
    scope = soup.select_one('[itemscope][itemtype$="/Event"]')
    if scope is None:
        raise ValueError("Missing CivicEngage event details")
    when = scope.select_one('.specificDetail[id$="_time"] .specificDetailItem')
    time_label = _text(when)
    if not time_label:
        raise ValueError("Missing displayed event time")
    if (time_label.casefold() == "all day") != event.content.all_day:
        raise ValueError("Calendar export and public detail disagree on all-day status")
    # The visible time range is authoritative about whether DTEND is a placeholder.
    clocks = re.findall(r"\b\d{1,2}:\d{2}\s*[AP]M\b", time_label, re.I)
    ends_at = event.content.ends_at
    if not event.content.all_day:
        if len(clocks) not in {1, 2}:
            raise ValueError("Unrecognized displayed CivicEngage time")
        if len(clocks) == 1:
            ends_at = None
        elif ends_at is None:
            component = Calendar.from_ical(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                + str(event.raw_payload["icalendar"])
                + "END:VCALENDAR\r\n"
            ).walk("VEVENT")[0]
            end = component.decoded("DTEND")
            if isinstance(end, datetime) and end.tzinfo is not None:
                ends_at = end.astimezone(UTC)
    description_node = scope.select_one('[itemprop="description"]')
    description = _text(description_node)
    venue_node = scope.select_one('[itemprop="location"] [itemprop="name"]')
    address_node = scope.select_one('[itemprop="location"] [itemprop="address"]')
    venue = ", ".join(part for part in (_text(venue_node), _text(address_node)) if part)
    registration = registration_link(scope, str(event.content.source_url))
    price_status, cost = cost_price(
        _text(scope.select_one('.specificDetail[id$="_cost"] .specificDetailItem'))
    )
    return replace(
        event,
        content=EventContent.model_validate(
            {
                **event.content.model_dump(),
                "description": description or event.content.description,
                "ends_at": ends_at,
                "venue": venue or event.content.venue,
                "location_kind": location_kind(venue),
                "registration_url": registration,
                "price_status": price_status,
                "price_details": cost,
            }
        ),
        raw_payload={
            **event.raw_payload,
            "displayed_time": time_label,
            "description_html": str(description_node) if description_node else None,
        },
    )


async def collect(
    client: httpx.AsyncClient,
    *,
    city: City,
    window_start: datetime,
    window_end: datetime,
) -> SourceCollection:
    """One official event category per city, with each dated occurrence kept distinct."""
    base, category = CALENDARS[city]
    feed_url = f"{base}/common/modules/iCalendar/iCalendar.aspx?catID={category}&feed=calendar"
    result = SourceCollection([], 0, [])
    try:
        calendar = Calendar.from_ical(await fetch_bytes(client, feed_url))
        components = calendar.walk("VEVENT")
        if len(components) > 2500:
            raise ValueError("CivicEngage export exceeds the 2500-event limit")
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        result.issues.append(ParseIssue(None, f"City calendar failed ({type(exc).__name__})"))
        return result
    result.records_seen = len(components)
    seen: set[str] = set()
    candidates: list[ParsedEvent] = []
    for component in components:
        identity = str(component.get("UID", "")) or None
        try:
            event = _parse(component, base)
            if event.external_id in seen:
                raise ValueError("Repeated event identity in city export")
            seen.add(event.external_id)
            if re.search(
                r"\b(?:closing day|registration (?:ends|deadline))\b", event.content.title, re.I
            ):
                continue
            if in_window(event.content, window_start, window_end):
                candidates.append(event)
            else:
                result.events.append(event)
        except (ValueError, TypeError, KeyError) as exc:
            result.issues.append(ParseIssue(identity, str(exc)))
    semaphore = asyncio.Semaphore(2)

    async def detail(event: ParsedEvent) -> ParsedEvent | ParseIssue:
        try:
            async with semaphore:
                await asyncio.sleep(0.5)
                html = await fetch_bytes(client, str(event.content.source_url))
            return _detail(html, event)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            message = "Invalid event fields" if isinstance(exc, ValidationError) else str(exc)
            return ParseIssue(event.external_id, message)

    for parsed in await asyncio.gather(*(detail(event) for event in candidates)):
        if isinstance(parsed, ParseIssue):
            result.issues.append(parsed)
        else:
            result.events.append(parsed)
    # The city currently publishes this one recycling collection twice, with
    # "City Hall" versus "City Hall parking lot" venue labels. Keep the newer
    # detailed listing only when both verified identities still agree on timing.
    if city == "lilburn":
        by_id = {event.external_id: event for event in result.events}
        older, newer = by_id.get("2099"), by_id.get("2115")
        if (
            older is not None
            and newer is not None
            and older.content.title == newer.content.title == "America Recycles"
            and older.content.starts_at == newer.content.starts_at
            and older.content.ends_at == newer.content.ends_at
            and all("Lilburn City Hall" in (event.content.venue or "") for event in (older, newer))
        ):
            retained = replace(
                newer,
                raw_payload={
                    **newer.raw_payload,
                    "duplicate_listing_id": older.external_id,
                    "duplicate_listing_url": str(older.content.source_url),
                    "duplicate_listing_icalendar": older.raw_payload["icalendar"],
                },
            )
            result.events = [
                retained if event.external_id == newer.external_id else event
                for event in result.events
                if event.external_id != older.external_id
            ]
            result.warnings.append("Collapsed the city's duplicate America Recycles listing.")
    result.events.sort(key=lambda event: event.external_id)
    return result
