"""Read Emory's public Trumba calendar, the whole import window in one request."""

from datetime import datetime, time
from html import unescape

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field, HttpUrl, JsonValue, TypeAdapter

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    LocationKind,
    PriceStatus,
    cost_price,
    described_price,
    http_url,
    image_url,
    text,
)
from app.ingestion.records import ParsedEvent, SourceCollection
from app.storage.models import ATLANTA, EventContent

FEED_URL = "https://www.trumba.com/calendars/emory-events.json"
_CALENDAR_URL = "https://www.trumba.com/calendars/emory-events"
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
    # Local wall times, each with its own UTC offset such as "-0400".
    startDateTime: str
    endDateTime: str
    startTimeZoneOffset: str = Field(pattern=r"^[+-]\d{4}$")
    endTimeZoneOffset: str = Field(pattern=r"^[+-]\d{4}$")
    allDay: bool = Field(strict=True)
    canceled: bool = Field(strict=True)
    requiresPayment: bool = Field(default=False, strict=True)
    location: str = ""
    locationType: str = ""
    signUpUrl: str = ""
    customFields: list[_CustomField] = Field(default_factory=list)


def _url(value: str) -> HttpUrl:
    if "<" in value:
        anchor = BeautifulSoup(value, "html.parser").find("a")
        value = str(anchor.get("href")) if isinstance(anchor, Tag) else ""
    return http_url(unescape(value.strip()))


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
    description = text(event.description)
    begins = datetime.fromisoformat(event.startDateTime + event.startTimeZoneOffset)
    finishes = datetime.fromisoformat(event.endDateTime + event.endTimeZoneOffset)
    start_date = end_date = None
    if event.allDay:
        local_start, local_end = begins.astimezone(ATLANTA), finishes.astimezone(ATLANTA)
        if local_start.time() != time.min or local_end.time() != time.min or finishes == begins:
            raise ValueError("All-day events need distinct midnight date boundaries")
        # Trumba's JSON end matches the exclusive DTEND;VALUE=DATE in its ICS feed.
        start_date, end_date = local_start.date(), local_end.date()
    registration = event.signUpUrl or fields.get("registration / r.s.v.p. link", "")
    price_status, price_details = _price(
        text(fields.get("cost", "")), event.requiresPayment, description
    )
    audience = [
        label.strip()
        for key in ("event open to", "intended audience")
        for label in text(fields.get(key, "")).split(",")
        if label.strip()
    ]
    tags = [
        label.strip()
        for key in ("university event topic", "event type", "economics event type", "type of art")
        for label in text(fields.get(key, "")).split(",")
        if label.strip()
    ]
    content = EventContent(
        title=text(event.title),
        description=description,
        starts_at=None if event.allDay else begins,
        ends_at=None if event.allDay or finishes == begins else finishes,
        all_day=event.allDay,
        start_date=start_date,
        end_date=end_date,
        venue=text(event.location) or None,
        location_kind=_LOCATION_TYPES.get(event.locationType, "unknown"),
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
        str(event.eventID),
        content,
        image_url=image_url(raw.get("detailImage") or raw.get("eventImage")),
    )


def parse_feed(payload: bytes) -> SourceCollection:
    """Normalize source fields without turning registration defaults into prices."""
    records = _JSON_EVENTS.validate_json(payload)
    result = SourceCollection(records_seen=len(records))
    for raw in records:
        with result.issue_if_fails(str(raw["eventID"]) if "eventID" in raw else None):
            result.events.append(_parse_item(raw))
    return result


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Fetch the whole window at once; the window holds far fewer events than the cap."""
    start = window_start.astimezone(ATLANTA).date()
    params = {
        "startdate": f"{start:%Y%m%d}",
        "days": str((window_end.astimezone(ATLANTA).date() - start).days + 1),
        "previousweeks": "0",
        "events": str(_PAGE_LIMIT),
    }
    result = parse_feed(await fetch_bytes(client, FEED_URL, params=params))
    if result.records_seen >= _PAGE_LIMIT:
        result.warnings.append(f"Emory returned its {_PAGE_LIMIT}-event cap; coverage is partial")
    return result
