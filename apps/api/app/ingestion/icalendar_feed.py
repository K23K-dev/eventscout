"""Normalize deliberately published, individually expanded iCalendar events."""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from icalendar import Calendar, Event
from pydantic import JsonValue, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import html_text, http_url, validation_message
from app.ingestion.records import ParsedEvent, ParsedFeed, ParseIssue, SourceCollection, in_window
from app.storage.models import EventContent

_TIMEZONE = ZoneInfo("America/New_York")


def calendar_text(event: Event, key: str) -> str:
    value = event.get(key)
    if isinstance(value, list):
        raise ValueError(f"Repeated {key} property")
    return str(value).strip() if value is not None else ""


def calendar_date(event: Event, key: str) -> date | datetime | None:
    if key not in event:
        return None
    value: object = event.decoded(key)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{key} must have an explicit timezone")
        return value.astimezone(UTC)
    if isinstance(value, date):
        return value
    raise ValueError(f"Invalid {key} property")


def calendar_categories(event: Event) -> list[str]:
    categories = event.get("CATEGORIES")
    groups = categories if isinstance(categories, list) else [categories] if categories else []
    return sorted({str(value).strip() for group in groups for value in group.cats if str(value)})


def calendar_events(data: bytes) -> list[Event]:
    try:
        calendar = Calendar.from_ical(data)
    except (ValueError, TypeError) as exc:
        raise ValueError("Publisher returned invalid iCalendar data") from exc
    if calendar.name != "VCALENDAR":
        raise ValueError("Expected a VCALENDAR subscription feed")
    return [part for part in calendar.walk("VEVENT") if isinstance(part, Event)]


def parse_calendar_events(
    components: list[Event],
    parse_event: Callable[[Event], ParsedEvent],
    *,
    skip_blank: bool = False,
) -> ParsedFeed:
    result = ParsedFeed(events=[], records_seen=len(components), issues=[])
    identities = Counter(str(part.get("UID", "")) for part in components)
    for part in components:
        uid = str(part.get("UID", ""))
        # Communico emits one entirely blank placeholder when a branch has no events.
        if skip_blank and not uid and not str(part.get("SUMMARY", "")):
            result.records_seen -= 1
            continue
        if identities[uid] > 1:
            result.issues.append(ParseIssue(uid or None, "Repeated calendar occurrence UID"))
            continue
        try:
            result.events.append(parse_event(part))
        except ValidationError as exc:
            result.issues.append(ParseIssue(uid or None, validation_message(exc)))
        except (ValueError, TypeError, AttributeError) as exc:
            result.issues.append(ParseIssue(uid or None, str(exc)))
    return result


def _parse_event(
    event: Event, *, fallback_url: str, event_base_url: str | None, midnight_all_day: bool
) -> ParsedEvent:
    uid = calendar_text(event, "UID")
    if not uid:
        raise ValueError("Missing stable calendar UID")
    if any(key in event for key in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID")):
        raise ValueError("Unexpanded calendar recurrence is unsupported")
    if "DURATION" in event:
        raise ValueError("Calendar duration needs an explicit supported end time")
    starts = calendar_date(event, "DTSTART")
    ends = calendar_date(event, "DTEND")
    published_end = ends
    if starts is None:
        raise ValueError("Missing DTSTART")
    if ends is not None and isinstance(starts, datetime) != isinstance(ends, datetime):
        raise ValueError("DTSTART and DTEND mix date-only and timed values")
    # An identical timed end is an export placeholder for an unknown duration.
    # Preserve the source value below while retaining the known start.
    if isinstance(starts, datetime) and starts == ends:
        ends = None
    if (
        midnight_all_day
        and isinstance(starts, datetime)
        and isinstance(ends, datetime)
        and starts.astimezone(_TIMEZONE).time() == time.min
        and ends.astimezone(_TIMEZONE).time() == time(23, 59)
    ):
        # Communico's exported 00:00–23:59 local interval corresponds to the
        # public detail page's explicit "All day" display, including DST changes.
        starts = starts.astimezone(_TIMEZONE).date()
        ends = ends.astimezone(_TIMEZONE).date() + timedelta(days=1)
    modified = calendar_date(event, "LAST-MODIFIED")
    if modified is not None and not isinstance(modified, datetime):
        raise ValueError("LAST-MODIFIED must be a timestamp")
    published_url = calendar_text(event, "URL")
    link = published_url or fallback_url
    if event_base_url:
        if not uid.isdecimal():
            raise ValueError("Expected a numeric library event ID")
        link = event_base_url + uid
    title = calendar_text(event, "SUMMARY")
    description = calendar_text(event, "DESCRIPTION")
    if re.search(r"</?(?:p|div|br|span|a|script|style)(?:\s|>|/)", description, re.I):
        description = html_text(description)
    venue = calendar_text(event, "LOCATION").strip(" -")
    if re.search(r"\bsign in\b|^tbd$|^tba$|^see (?:the )?website$", venue, re.I):
        venue = ""
    location_kind: Literal["in_person", "online", "hybrid", "unknown"] = "unknown"
    if venue:
        location_kind = (
            "online" if re.match(r"^(?:online|virtual|zoom)\b", venue, re.I) else "in_person"
        )
    tags = calendar_categories(event)
    raw_tags: list[JsonValue] = list(tags)
    status = calendar_text(event, "STATUS").upper()
    if status not in {"", "CONFIRMED", "TENTATIVE", "CANCELLED"}:
        raise ValueError("Unsupported calendar status")
    all_day = not isinstance(starts, datetime)
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts if isinstance(starts, datetime) else None,
        ends_at=ends if isinstance(ends, datetime) else None,
        all_day=all_day,
        start_date=starts if all_day else None,
        end_date=(ends or starts + timedelta(days=1)) if all_day else None,
        timezone="America/New_York",
        venue=venue or None,
        location_kind=location_kind,
        region="atlanta",
        tags=tags,
        source_url=http_url(link),
        status="cancelled"
        if status == "CANCELLED" or re.match(r"^(?:\[|\()?(?:cancelled|canceled)\b", title, re.I)
        else "scheduled",
    )
    return ParsedEvent(
        external_id=uid,
        content=content,
        source_updated_at=modified,
        raw_payload={
            "uid": uid,
            "summary": title,
            "description": description,
            "location": calendar_text(event, "LOCATION"),
            "url": link,
            "dtstart": starts.isoformat(),
            "dtend": published_end.isoformat() if published_end else None,
            "last_modified": modified.isoformat() if modified else None,
            "categories": raw_tags,
            "status": status,
            "source_url_is_collection": not published_url and event_base_url is None,
        },
    )


def parse_feed(
    data: bytes,
    *,
    fallback_url: str,
    event_base_url: str | None = None,
    midnight_all_day: bool = False,
) -> ParsedFeed:
    return parse_calendar_events(
        calendar_events(data),
        lambda event: _parse_event(
            event,
            fallback_url=fallback_url,
            event_base_url=event_base_url,
            midnight_all_day=midnight_all_day,
        ),
        skip_blank=bool(event_base_url),
    )


async def collect(
    client: httpx.AsyncClient,
    *,
    window_start: datetime,
    window_end: datetime,
    url: str,
    source_page: str,
    require_metro_venue: bool = False,
) -> SourceCollection:
    parsed = parse_feed(await fetch_bytes(client, url), fallback_url=source_page)
    events = [
        replace(event, raw_payload={**event.raw_payload, "feed_urls": [url]})
        for event in parsed.events
        if in_window(event.content, window_start, window_end)
    ]
    if require_metro_venue:
        # The radio calendar also advertises travel to national hamfests. Retain
        # explicit Atlanta-metro venues; ambiguous statewide activities stay out.
        events = [
            event
            for event in events
            if re.search(
                r"\b(?:Atlanta|Lawrenceville|Gwinnett|Peachtree City|Fayetteville|Roswell|"
                r"Marietta|Decatur|Kennesaw|Moreland|Dacula|Panola Mountain)\b",
                event.content.venue or "",
                re.I,
            )
        ]
    return SourceCollection(
        events=events,
        records_seen=parsed.records_seen,
        requests=1,
        issues=parsed.issues,
    )
