"""Source observations shared by the calendar adapters."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.storage.models import EventContent

# What a failed download or malformed publisher data raises while one record is read.
_RECORD_ERRORS = (httpx.HTTPError, TimeoutError, ValueError, TypeError, LookupError, AttributeError)


@dataclass(frozen=True)
class ParsedEvent:
    external_id: str
    content: EventContent
    # The publisher's own revision time, when the feed has one.
    source_updated_at: datetime | None = None
    image_url: str | None = None
    # The source URL is a listing page shared by many events, so matching can't key on it.
    source_url_is_collection: bool = False


@dataclass(frozen=True)
class ParseIssue:
    external_id: str | None
    message: str


@dataclass
class SourceCollection:
    """A calendar's events, issues and warnings; the runner merges repeated event IDs."""

    events: list[ParsedEvent] = field(default_factory=list)
    records_seen: int = 0
    issues: list[ParseIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @contextmanager
    def issue_if_fails(self, external_id: str | None) -> Iterator[None]:
        """Report a record or page that fails to download or parse, then carry on."""
        try:
            yield
        except _RECORD_ERRORS as exc:
            self.issues.append(ParseIssue(external_id, issue_message(exc)))


def issue_message(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, ValidationError):
        fields = (".".join(map(str, error["loc"])) or "URL" for error in exc.errors())
        return f"Invalid event fields: {', '.join(fields)}"
    return str(exc) if isinstance(exc, ValueError) else type(exc).__name__


def in_window(content: EventContent, start: datetime, end: datetime) -> bool:
    """Include ongoing events in a half-open window, after merging source revisions."""
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
