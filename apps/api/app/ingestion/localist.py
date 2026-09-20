"""Read deliberately published Localist iCalendar subscription feeds.

Localist's public export is capped by parent events, rather than VEVENT instances.
GSU's export currently ignores the date filters included in its subscription URLs;
fetch it once, filter locally, and report the cap instead of implying full coverage.
"""

import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

import httpx
from icalendar import Calendar, Event
from pydantic import HttpUrl, JsonValue, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.records import ParsedEvent, ParsedFeed, ParseIssue, SourceCollection, in_window
from app.storage.models import EventContent

FEED_URL = "https://calendar.gsu.edu/calendar/1.ics"
_PARENT_EXPORT_LIMIT = 1000
_BARE_OBSERVANCES = {
    "advent",
    "eastern orthodox christmas",
    "hanukkah",
    "sukkot",
    "simchat torah & shemini atzeret",
    "birth of the bab (26) and baha'u'llah (27)",
    "yom kippur",
    "harvest, or mabon",
    "dussehra, or dasara",
    "samhain",
    "all saints’ day",
    "diwali",
    "feast of the immaculate conception",
}


def _text(event: Event, name: str) -> str:
    value = event.get(name)
    if isinstance(value, list):
        raise ValueError(f"Repeated {name} property is ambiguous")
    return str(value).strip() if value is not None else ""


def _date_value(event: Event, name: str) -> date | datetime | None:
    if name not in event:
        return None
    value: object = event.decoded(name)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"Floating {name} timestamp has no timezone")
        return value.astimezone(UTC)
    if isinstance(value, date):
        return value
    raise ValueError(f"Invalid {name} calendar value")


def _categories(event: Event) -> list[str]:
    values = event.get("CATEGORIES")
    if values is None:
        return []
    groups = values if isinstance(values, list) else [values]
    return sorted({str(value).strip() for group in groups for value in group.cats if str(value)})


def _location_kind(venue: str) -> Literal["in_person", "online", "hybrid", "unknown"]:
    if not venue or venue.casefold() in {"tba", "tbd", "see description"}:
        return "unknown"
    if re.search(r"\bhybrid\b", venue, re.I):
        return "hybrid"
    if re.fullmatch(r"(?:online|virtual|zoom|webinar|microsoft teams)", venue, re.I):
        return "online"
    if re.search(r"\b(?:online|virtual|zoom)\b", venue, re.I):
        return "unknown"
    return "in_person"


def _parse_event(event: Event, publisher_host: str) -> ParsedEvent:
    external_id = _text(event, "UID")
    if not re.fullmatch(r"tag:localist\.com,2008:EventInstance_\d+", external_id):
        raise ValueError("Missing stable Localist event-instance UID")
    if any(name in event for name in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID")):
        raise ValueError("Expected individually expanded Localist event instances")
    starts = _date_value(event, "DTSTART")
    ends = _date_value(event, "DTEND")
    if starts is None:
        raise ValueError("Missing DTSTART")
    if "DURATION" in event:
        raise ValueError("Calendar duration needs an explicit supported end time")
    all_day = not isinstance(starts, datetime)
    if ends is not None and isinstance(ends, datetime) == all_day:
        raise ValueError("DTSTART and DTEND mix date-only and timed values")
    url = _text(event, "URL")
    parts = urlsplit(url)
    if (
        parts.scheme not in {"https", "http"}
        or parts.hostname != publisher_host
        or parts.username
        or parts.password
    ):
        raise ValueError("Expected an event URL on its public calendar publisher")
    updated = _date_value(event, "LAST-MODIFIED")
    if updated is not None and not isinstance(updated, datetime):
        raise ValueError("LAST-MODIFIED must be a timestamp")
    title = _text(event, "SUMMARY")
    description = _text(event, "DESCRIPTION")
    venue = _text(event, "LOCATION")
    tags = _categories(event)
    status = _text(event, "STATUS").upper()
    if status not in {"", "CONFIRMED", "TENTATIVE", "CANCELLED"}:
        raise ValueError("Unsupported calendar event status")
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts if isinstance(starts, datetime) else None,
        ends_at=ends if isinstance(ends, datetime) else None,
        all_day=all_day,
        start_date=starts if not isinstance(starts, datetime) else None,
        end_date=(ends or starts + timedelta(days=1))
        if all_day and not isinstance(ends, datetime)
        else None,
        timezone="America/New_York",
        venue=venue or None,
        location_kind=_location_kind(venue),
        region="atlanta",
        tags=tags,
        source_url=HttpUrl(url),
        status="cancelled"
        if status == "CANCELLED" or re.match(r"^(?:\[|\()?(?:cancelled|canceled)\b", title, re.I)
        else "scheduled",
    )
    raw_tags: list[JsonValue] = list(tags)
    return ParsedEvent(
        external_id=external_id,
        content=content,
        # DTSTAMP is export-generation time here; it is not a source content revision.
        source_updated_at=updated,
        raw_payload={
            "uid": external_id,
            "summary": title,
            "description": description,
            "location": venue,
            "url": url,
            "status": status,
            "categories": raw_tags,
            "dtstart": starts.isoformat(),
            "dtend": ends.isoformat() if ends else None,
            "last_modified": updated.isoformat() if updated else None,
        },
    )


def parse_feed(data: bytes, *, publisher_host: str = "calendar.gsu.edu") -> tuple[ParsedFeed, int]:
    """Parse expanded occurrences without conflating a series or its moving dates."""
    try:
        calendar = Calendar.from_ical(data)
    except (ValueError, TypeError) as exc:
        raise ValueError("Publisher returned invalid iCalendar data") from exc
    if calendar.name != "VCALENDAR":
        raise ValueError("Expected a VCALENDAR subscription feed")
    components = [
        component for component in calendar.walk("VEVENT") if isinstance(component, Event)
    ]
    result = ParsedFeed(events=[], records_seen=len(components), issues=[])
    identities = Counter(str(component.get("UID", "")) for component in components)
    parent_urls = {str(component.get("URL", "")) for component in components} - {""}
    for component in components:
        external_id = str(component.get("UID", ""))
        if identities[external_id] > 1:
            result.issues.append(ParseIssue(external_id or None, "Repeated event-instance UID"))
            continue
        try:
            result.events.append(_parse_event(component, publisher_host))
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(str(part) for part in error["loc"]) or "URL"
                for error in exc.errors(include_input=False)
            )
            result.issues.append(ParseIssue(external_id or None, f"Invalid event fields: {fields}"))
        except (ValueError, TypeError, AttributeError) as exc:
            result.issues.append(ParseIssue(external_id or None, str(exc)))
    return result, len(parent_urls)


def _discovery_event(event: ParsedEvent) -> bool:
    content = event.content
    # Administrative registration/semester dates are not activities to attend.
    if {tag.casefold() for tag in content.tags} & {"academic calendar", "academic dates"}:
        return False
    if content.all_day and re.fullmatch(
        r"(?:(?:fall|spring|winter|summer) break(?: for students)?|final exams?|"
        r"(?:first|last) day of classes(?: for .*)?|registration(?: period)?|"
        r".*(?:application|submission) deadline)",
        content.title,
        re.I,
    ):
        return False
    if content.all_day and re.search(
        r"\b(?:p-card cycle|re-allocations in works|submission deadline|abstracts due|"
        r"final exams|reading day|last day of classes|conferral date|fall break for students)\b",
        content.title,
        re.I,
    ):
        return False
    if (
        content.all_day
        and not content.venue
        and content.title.casefold() in _BARE_OBSERVANCES | {"black cat week"}
    ):
        return False
    # These inspected source listings describe closed performance preparation.
    # Keep auditions, open rehearsals, and academic practice workshops.
    if content.title.casefold() in {
        "blackfriars student theatre rehearsals",
        "royalette practice",
        "luchsinger a cappella rehearsal",
        "luchsinger rehersal",
    } and not re.search(r"\b(?:open to the public|open rehearsal)\b", content.description, re.I):
        return False
    # GSU's institutional calendar includes national away fixtures. Preserve home
    # sports; avoid presenting away fixtures as Atlanta events without geocoding.
    sports_title = re.match(
        r"^(?:Georgia State University |Kennesaw State University |Kennesaw State |"
        r"Agnes Scott College |Agnes Scott )?"
        r"(?:Men['’]s|Women['’]s|Football|Volleyball|Baseball|Softball|Beach Volleyball|"
        r"Soccer|Golf|Tennis|Cross Country|Track(?: and| &) Field)\b",
        content.title,
        re.I,
    )
    if sports_title:
        venue = content.venue or ""
        local_venue = re.match(
            r"^(?:Atlanta|Kennesaw|Decatur|Marietta|Smyrna|Roswell|Alpharetta|"
            r"Dunwoody|Clarkston|College Park|East Point|Fairburn|Fairbuurn|Carrollton)\b",
            venue,
            re.I,
        )
        if local_venue:
            return True
        # Neutral-site fixtures can say "vs" while taking place outside Georgia.
        if re.search(
            r",\s*(?:N\.?\s?C\.?|S\.?\s?C\.?|Fla?\.?|FL|Tenn\.?|TN|Ala?\.?|AL|"
            r"Ark?\.?|AR|Ky\.?|KY|Miss\.?|MS|Va\.?|VA|WV|W\.?\s?Va\.?|"
            r"Texas|TX|La\.?|LA|Mich\.?|MI|Kan\.?|KS|Mo\.?|MO|Ind\.?|IN|"
            r"Del\.?|DE|Calif\.?|CA|Ariz\.?|AZ|N\.?\s?Y\.?|NY)(?:\s|,|$)",
            venue,
            re.I,
        ) or re.search(r"\bat\b", content.title, re.I):
            return False
    return True


async def collect(
    client: httpx.AsyncClient,
    *,
    window_start: datetime,
    window_end: datetime,
    url: str = FEED_URL,
) -> SourceCollection:
    host = urlsplit(url).hostname
    if host not in {"calendar.gsu.edu", "calendar.kennesaw.edu", "calendar.agnesscott.edu"}:
        raise ValueError("Unsupported public Localist calendar publisher")
    parsed, parent_count = parse_feed(await fetch_bytes(client, url), publisher_host=host)
    if parsed.records_seen == 0:
        raise ValueError("Publisher returned an unexpectedly empty subscription feed")
    return SourceCollection(
        events=[
            event
            for event in parsed.events
            if _discovery_event(event) and in_window(event.content, window_start, window_end)
        ],
        records_seen=parsed.records_seen,
        requests=1,
        issues=parsed.issues,
        warnings=[
            f"Public subscription export contains {parent_count:,} parent events and "
            f"{parsed.records_seen:,} occurrences; it reached the 1,000 parent-event "
            "limit, so coverage is partial"
        ]
        if parent_count >= _PARENT_EXPORT_LIMIT
        else [],
    )
