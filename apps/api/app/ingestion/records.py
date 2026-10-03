"""Source observations shared by the calendar adapters."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from app.storage.models import EventContent


@dataclass(frozen=True)
class ParsedEvent:
    external_id: str
    content: EventContent
    source_updated_at: datetime | None
    raw_payload: dict[str, JsonValue]


@dataclass(frozen=True)
class ParseIssue:
    external_id: str | None
    message: str


@dataclass
class ParsedFeed:
    events: list[ParsedEvent]
    records_seen: int
    issues: list[ParseIssue]


@dataclass
class SourceCollection:
    """Fetched observations; the runner admits new events only inside its window.

    Keep already-fetched dates outside the window so known occurrences can move.
    """

    events: list[ParsedEvent]
    records_seen: int
    issues: list[ParseIssue]
    warnings: list[str] = field(default_factory=list)


def in_window(content: EventContent, start: datetime, end: datetime) -> bool:
    """Include ongoing events in a half-open window, after merging source revisions."""
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end)):
        raise ValueError("The ingestion window must have timezone-aware boundaries")
    if end <= start:
        raise ValueError("The ingestion window must end after it starts")
    if content.start_date is not None:
        timezone = ZoneInfo(content.timezone)
        begins = datetime.combine(content.start_date, time.min, timezone)
        finishes = datetime.combine(
            content.end_date or content.start_date + timedelta(days=1), time.min, timezone
        )
        return begins < end and finishes > start
    if content.starts_at is None:
        return False
    return content.starts_at < end and (
        content.ends_at > start if content.ends_at else content.starts_at >= start
    )
