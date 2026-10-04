"""Merge public branch subscriptions into one calendar per library system."""

import asyncio
import base64
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from icalendar import Event
from pydantic import JsonValue

from app.ingestion.http import fetch_bytes
from app.ingestion.icalendar_feed import (
    calendar_categories,
    calendar_date,
    calendar_events,
    calendar_text,
    parse_calendar_events,
)
from app.ingestion.parsing import http_url, location_kind
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

CALENDARS = {
    "dekalb": "https://events.dekalblibrary.org",
    "gwinnettpl": "https://gwinnettpl.libnet.info",
}
_FEED_LIMIT = 500
_TIMEZONE = ZoneInfo("America/New_York")


def _feed_params(location_id: str) -> dict[str, str]:
    # Same payload as the publisher's Add to Calendar control. Its UI explicitly
    # documents a 500-event cap and that the selected date range is not exported.
    filters = {
        "feedType": "ical",
        "filters": {
            "location": [location_id],
            "ages": ["all"],
            "types": ["all"],
            "tags": [],
            "term": "",
            "days": 1,
        },
    }
    return {"data": base64.b64encode(json.dumps(filters, separators=(",", ":")).encode()).decode()}


def _parse_event(event: Event, base: str) -> ParsedEvent:
    uid = calendar_text(event, "UID")
    if not uid.isdecimal():
        raise ValueError("Expected a numeric library event ID")
    if any(key in event for key in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID")):
        raise ValueError("Unexpanded calendar recurrence is unsupported")
    if "DURATION" in event:
        raise ValueError("Calendar duration needs an explicit supported end time")
    starts = calendar_date(event, "DTSTART")
    ends = published_end = calendar_date(event, "DTEND")
    if starts is None:
        raise ValueError("Missing DTSTART")
    if ends is not None and isinstance(starts, datetime) != isinstance(ends, datetime):
        raise ValueError("DTSTART and DTEND mix date-only and timed values")
    if (
        isinstance(starts, datetime)
        and isinstance(ends, datetime)
        and starts.astimezone(_TIMEZONE).time() == time.min
        and ends.astimezone(_TIMEZONE).time() == time(23, 59)
    ):
        # The exported 00:00–23:59 local interval corresponds to the public
        # detail page's explicit "All day" display, including DST changes.
        starts = starts.astimezone(_TIMEZONE).date()
        ends = ends.astimezone(_TIMEZONE).date() + timedelta(days=1)
    modified = calendar_date(event, "LAST-MODIFIED")
    if modified is not None and not isinstance(modified, datetime):
        raise ValueError("LAST-MODIFIED must be a timestamp")
    link = f"{base}/event/{uid}"
    title = calendar_text(event, "SUMMARY")
    description = calendar_text(event, "DESCRIPTION")
    location = calendar_text(event, "LOCATION")
    # Rooms follow the branch ("Decatur Library - Auditorium"); some events leave it blank.
    venue = location.strip(" -")
    tags = calendar_categories(event)
    raw_tags: list[JsonValue] = list(tags)
    status = calendar_text(event, "STATUS").upper()
    all_day = not isinstance(starts, datetime)
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts if isinstance(starts, datetime) else None,
        ends_at=ends if isinstance(ends, datetime) else None,
        all_day=all_day,
        start_date=starts if all_day else None,
        end_date=(ends or starts + timedelta(days=1)) if all_day else None,
        venue=venue or None,
        location_kind=location_kind(venue),
        tags=tags,
        source_url=http_url(link),
        status="cancelled" if status == "CANCELLED" else "scheduled",
    )
    return ParsedEvent(
        external_id=uid,
        content=content,
        source_updated_at=modified,
        raw_payload={
            "uid": uid,
            "summary": title,
            "description": description,
            "location": location,
            "url": link,
            "dtstart": starts.isoformat(),
            "dtend": published_end.isoformat() if published_end else None,
            "last_modified": modified.isoformat() if modified else None,
            "categories": raw_tags,
            "status": status,
        },
    )


async def collect(
    client: httpx.AsyncClient,
    *,
    window_start: datetime,
    window_end: datetime,
    library: str,
) -> SourceCollection:
    base = CALENDARS[library]
    # The event listing itself loads this public location metadata to construct
    # branch filters. No credentials or event-registration endpoints are used.
    data = await fetch_bytes(client, f"https://api.communico.co/v1/{library}/locations")
    locations: object = json.loads(data)
    if not isinstance(locations, list) or not 1 <= len(locations) <= 100:
        raise ValueError("Unexpected public library location list")
    branches: dict[str, str] = {}
    for location in locations:
        identity, name = str(location.get("id", "")), str(location.get("name", ""))
        if not identity.isdecimal() or not name or identity in branches:
            raise ValueError("Missing or repeated library location identity")
        branches[identity] = name
    result = SourceCollection()
    merged: dict[str, ParsedEvent] = {}
    conflicts: set[str] = set()
    # All-events also catches an event whose location has not reached metadata yet.
    for location_id, name in {"all": "All locations", **branches}.items():
        await asyncio.sleep(1)
        try:
            data = await fetch_bytes(client, f"{base}/feeds", params=_feed_params(location_id))
            # A branch with no events exports one entirely blank placeholder.
            components = [
                part
                for part in calendar_events(data)
                if str(part.get("UID", "")) or str(part.get("SUMMARY", ""))
            ]
        except (ValueError, httpx.HTTPError, TimeoutError) as exc:
            result.issues.append(
                ParseIssue(None, f"{name}: download failed ({type(exc).__name__})")
            )
            continue
        parsed = parse_calendar_events(components, lambda event: _parse_event(event, base))
        result.records_seen += parsed.records_seen
        for issue in parsed.issues:
            if issue not in result.issues:
                result.issues.append(issue)
        if location_id != "all" and parsed.records_seen >= _FEED_LIMIT:
            result.warnings.append(
                f"{name} reached its 500-event export cap; coverage may be partial"
            )
        for event in parsed.events:
            previous = merged.get(event.external_id)
            if previous is not None and previous.content != event.content:
                conflicts.add(event.external_id)
                continue
            merged[event.external_id] = event
    result.issues.extend(
        ParseIssue(identity, "Conflicting versions across branch subscriptions")
        for identity in sorted(conflicts)
    )
    result.events = [event for identity, event in merged.items() if identity not in conflicts]
    return result
