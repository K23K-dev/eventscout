"""Read individually expanded events from deliberately published iCalendar feeds."""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime

from icalendar import Calendar, Event
from pydantic import ValidationError

from app.ingestion.parsing import validation_message
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection


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
    return value if isinstance(value, date) else None


def calendar_categories(event: Event) -> list[str]:
    categories = event.get("CATEGORIES")
    groups = categories if isinstance(categories, list) else [categories] if categories else []
    return sorted({str(value).strip() for group in groups for value in group.cats if str(value)})


def calendar_events(data: bytes) -> list[Event]:
    try:
        calendar = Calendar.from_ical(data)
    except (ValueError, TypeError) as exc:
        raise ValueError("Publisher returned invalid iCalendar data") from exc
    return [part for part in calendar.walk("VEVENT") if isinstance(part, Event)]


def parse_calendar_events(
    components: list[Event], parse_event: Callable[[Event], ParsedEvent]
) -> SourceCollection:
    result = SourceCollection(records_seen=len(components))
    identities = Counter(str(part.get("UID", "")) for part in components)
    for part in components:
        uid = str(part.get("UID", ""))
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
