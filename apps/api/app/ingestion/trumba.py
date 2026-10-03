"""Read Emory's public Trumba calendar with bounded, complete date slices."""

import asyncio
import re
from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field, HttpUrl, JsonValue, TypeAdapter, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    LocationKind,
    PriceStatus,
    cost_price,
    described_price,
    http_url,
    validation_message,
)
from app.ingestion.parsing import text as _text
from app.ingestion.records import ParsedEvent, ParsedFeed, ParseIssue, SourceCollection
from app.storage.models import EventContent

FEED_URL = "https://www.trumba.com/calendars/emory-events.json"
_CALENDAR_URL = "https://www.trumba.com/calendars/emory-events"
_TIMEZONE = ZoneInfo("America/New_York")
# The published-events OpenAPI documents events=1..1000. Emory's default is only 200.
_PAGE_LIMIT = 1000
_JSON_EVENTS = TypeAdapter(list[dict[str, JsonValue]])
_LOCATION_TYPES: dict[str, LocationKind] = {
    "In-Person": "in_person",
    "Online": "online",
    "Hybrid": "hybrid",
}


class _CustomField(BaseModel):
    label: str
    value: str


class _Event(BaseModel):
    eventID: int = Field(strict=True, gt=0)
    title: str
    description: str = ""
    startDateTime: str
    endDateTime: str
    startTimeZoneOffset: str
    endTimeZoneOffset: str
    allDay: bool = Field(strict=True)
    canceled: bool = Field(strict=True)
    requiresPayment: bool = Field(default=False, strict=True)
    location: str = ""
    locationType: str = ""
    signUpUrl: str = ""
    customFields: list[_CustomField] = Field(default_factory=list)


def _url(value: str) -> HttpUrl:
    href: object = value.strip()
    if "<" in value:
        anchor = BeautifulSoup(value, "html.parser").find("a")
        href = anchor.get("href") if isinstance(anchor, Tag) else None
    if not isinstance(href, str):
        raise ValueError("Registration link has no URL")
    return http_url(unescape(href))


def _timestamp(value: str, offset: str) -> datetime:
    if not re.fullmatch(r"[+-]\d{4}", offset):
        raise ValueError("Event time needs an explicit UTC offset")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("Expected a local Trumba timestamp with a separate UTC offset")
    aware = datetime.fromisoformat(value + offset)
    # The JSON uses local wall time, verified against Emory's ICS feed. Validate
    # the supplied offset independently, including both sides of DST changes.
    if aware.astimezone(_TIMEZONE).replace(tzinfo=None) != parsed:
        raise ValueError("Event UTC offset does not match the Emory calendar timezone")
    return aware.astimezone(UTC)


def _price(cost: str, payment_required: bool, description: str) -> tuple[PriceStatus, str | None]:
    if payment_required:
        return "paid", cost or "Payment required by the registration provider"
    status, details = cost_price(cost)
    if status != "unknown" or details:
        return status, details
    # requiresPayment=false only describes Trumba registration, not event admission.
    return described_price(description)


def _parse_item(raw: dict[str, JsonValue]) -> ParsedEvent:
    event = _Event.model_validate(raw)
    fields: dict[str, str] = {}
    for field in event.customFields:
        label = field.label.casefold().strip()
        if label in fields and fields[label] != field.value:
            raise ValueError(f"Conflicting custom field: {field.label}")
        fields[label] = field.value
    description = _text(event.description)
    begins = _timestamp(event.startDateTime, event.startTimeZoneOffset)
    finishes = _timestamp(event.endDateTime, event.endTimeZoneOffset)
    if finishes < begins:
        raise ValueError("Event end precedes its start")
    start_date = end_date = None
    if event.allDay:
        local_start, local_end = begins.astimezone(_TIMEZONE), finishes.astimezone(_TIMEZONE)
        if local_start.time() != time.min or local_end.time() != time.min or finishes == begins:
            raise ValueError("All-day events need distinct midnight date boundaries")
        # Trumba's JSON end matches the exclusive DTEND;VALUE=DATE in its ICS feed.
        start_date, end_date = local_start.date(), local_end.date()
    registration = event.signUpUrl or fields.get("registration / r.s.v.p. link", "")
    price_status, price_details = _price(
        _text(fields.get("cost", "")), event.requiresPayment, description
    )
    audience = [
        label.strip()
        for key in ("event open to", "intended audience")
        for label in _text(fields.get(key, "")).split(",")
        if label.strip()
    ]
    tags = [
        label.strip()
        for key in ("university event topic", "event type", "economics event type", "type of art")
        for label in _text(fields.get(key, "")).split(",")
        if label.strip()
    ]
    content = EventContent(
        title=_text(event.title),
        description=description,
        starts_at=None if event.allDay else begins,
        ends_at=None if event.allDay or finishes == begins else finishes,
        all_day=event.allDay,
        start_date=start_date,
        end_date=end_date,
        timezone=_TIMEZONE.key,
        venue=_text(event.location) or None,
        location_kind=_LOCATION_TYPES.get(event.locationType, "unknown"),
        region="atlanta",
        price_status=price_status,
        price_details=price_details,
        audience=audience,
        tags=tags,
        source_url=HttpUrl(f"{_CALENDAR_URL}?eventid={event.eventID}"),
        registration_url=_url(registration) if registration else None,
        status="cancelled" if event.canceled else "scheduled",
    )
    return ParsedEvent(
        # Repeating occurrences have individual eventIDs; seriesID groups them.
        external_id=str(event.eventID),
        content=content,
        source_updated_at=None,
        raw_payload={"event": raw},
    )


def parse_feed(payload: bytes) -> ParsedFeed:
    """Normalize source fields without turning registration defaults into prices."""
    try:
        records = _JSON_EVENTS.validate_json(payload)
    except ValidationError as exc:
        raise ValueError("Expected a Trumba JSON array of event objects") from exc
    result = ParsedFeed(events=[], records_seen=len(records), issues=[])
    for raw in records:
        external_id = str(raw["eventID"]) if "eventID" in raw else None
        try:
            result.events.append(_parse_item(raw))
        except ValidationError as exc:
            result.issues.append(ParseIssue(external_id, validation_message(exc)))
        except ValueError as exc:
            result.issues.append(ParseIssue(external_id, str(exc)))
    return result


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Fetch weekly slices, splitting a full response down to individual days.

    A saturated day or failed request is explicitly incomplete. The caller merges
    overlapping ongoing events by eventID and applies the precise time window.
    """
    if any(
        value.tzinfo is None or value.utcoffset() is None for value in (window_start, window_end)
    ):
        raise ValueError("The ingestion window must have timezone-aware boundaries")
    if window_end <= window_start:
        raise ValueError("The ingestion window must end after it starts")
    local_end = window_end.astimezone(_TIMEZONE)
    finish = local_end.date() + timedelta(days=int(local_end.time() != time.min))
    cursor = window_start.astimezone(_TIMEZONE).date()
    slices: list[tuple[date, int]] = []
    while cursor < finish:
        days = min(7, (finish - cursor).days)
        slices.append((cursor, days))
        cursor += timedelta(days=days)
    semaphore = asyncio.Semaphore(3)

    async def fetch_slice(start: date, days: int) -> SourceCollection:
        params = {
            "startdate": start.strftime("%Y%m%d"),
            "days": str(days),
            "previousweeks": "0",
            "events": str(_PAGE_LIMIT),
        }
        result = SourceCollection(events=[], records_seen=0, issues=[])
        try:
            async with semaphore:
                payload = await fetch_bytes(client, FEED_URL, params=params)
            parsed = parse_feed(payload)
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            reason = (
                f"HTTP {exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else type(exc).__name__
            )
            result.issues.append(ParseIssue(None, f"Emory slice {start} ({days} days): {reason}"))
            return result
        if parsed.records_seen >= _PAGE_LIMIT and days > 1:
            left = days // 2
            children = await asyncio.gather(
                fetch_slice(start, left), fetch_slice(start + timedelta(days=left), days - left)
            )
            for child in children:
                result.events.extend(child.events)
                result.records_seen += child.records_seen
                result.issues.extend(child.issues)
            return result
        result.events = parsed.events
        result.records_seen = parsed.records_seen
        result.issues.extend(parsed.issues)
        if parsed.records_seen >= _PAGE_LIMIT:
            result.issues.append(
                ParseIssue(
                    None, f"Emory slice {start} reached {_PAGE_LIMIT} events; incomplete day"
                )
            )
        return result

    collections = await asyncio.gather(*(fetch_slice(start, days) for start, days in slices))
    return SourceCollection(
        events=[event for collection in collections for event in collection.events],
        records_seen=sum(collection.records_seen for collection in collections),
        issues=[issue for collection in collections for issue in collection.issues],
    )
