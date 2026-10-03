"""The public catalog contract and its validated search parameters."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, model_validator

from app.storage.models import EventContent, SourceInput

CATALOG_TIMEZONE = ZoneInfo("America/New_York")


# The categories enrichment assigns, 1-3 per event.
Topic = Literal[
    "music",
    "theater",
    "dance",
    "comedy",
    "film",
    "visual art",
    "books and writing",
    "talks and lectures",
    "classes and workshops",
    "technology",
    "science",
    "business and networking",
    "careers",
    "sports",
    "fitness and wellness",
    "outdoors and nature",
    "volunteering",
    "family and kids",
    "food and drink",
    "community and culture",
    "faith",
    "student life",
    "health",
]


class EventFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    q: str | None = Field(
        default=None,
        max_length=200,
        description="Keywords in the title, venue, or description; supports quoted phrases and OR.",
    )
    date_from: date = Field(
        default_factory=lambda: datetime.now(CATALOG_TIMEZONE).date(),
        ge=date(1900, 1, 1),
        le=date(2100, 1, 1),
        description="Inclusive first calendar date in America/New_York; defaults to today.",
    )
    date_to: date | None = Field(
        default=None,
        ge=date(1900, 1, 1),
        le=date(2100, 4, 1),
        description="Exclusive end date; defaults to date_from + 30 days. Maximum span: 90 days.",
    )
    region: Literal["gt", "atlanta"] | None = None
    location_kind: Literal["in_person", "online", "hybrid", "unknown"] | None = None
    price_status: Literal["free", "paid", "conditional", "unknown"] | None = Field(
        default=None, description="Exact stated price category. Unknown prices never match free."
    )
    venue: str | None = Field(
        default=None, max_length=200, description="Literal, case-insensitive venue substring."
    )
    topic: Topic | None = Field(default=None, description="Only events enrichment tagged with it.")
    sort: Literal["relevance", "start_time"] = Field(
        default="relevance",
        description="Relevance uses keyword rank, then start time and event ID.",
    )
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def end_date(self) -> date:
        return self.date_to if self.date_to is not None else self.date_from + timedelta(days=30)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if not 1 <= (self.end_date - self.date_from).days <= 90:
            raise ValueError("date_to must be after date_from and no more than 90 days later")
        return self


class EventSource(SourceInput):
    """A public calendar that lists the event."""


class EventResponse(EventContent):
    id: UUID
    content_version: int
    last_verified_at: AwareDatetime | None = Field(
        default=None, description="Last verification by the source supplying the event details."
    )
    sources: list[EventSource]
    summary: str | None = Field(default=None, description="One-line summary written by enrichment.")
    topics: list[str] = Field(default_factory=list, description="1-3 enrichment Topic values.")
    image_url: str | None = Field(default=None, description="A picture the calendar published.")

    @property
    def first_day(self) -> date | None:
        """The Atlanta calendar date the event starts on."""
        if self.start_date is not None:
            return self.start_date
        return self.starts_at.astimezone(CATALOG_TIMEZONE).date() if self.starts_at else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_stale(self) -> bool:
        if self.last_verified_at is None:
            return True
        now = datetime.now(UTC)
        start, end = self.starts_at, self.ends_at
        if self.all_day and self.start_date:
            zone = ZoneInfo(self.timezone)
            start = datetime.combine(self.start_date, time.min, zone).astimezone(UTC)
            end = datetime.combine(
                self.end_date or self.start_date + timedelta(days=1), time.min, zone
            ).astimezone(UTC)
        near = start is not None and (
            now <= start <= now + timedelta(days=7)
            or (start <= now and end is not None and end > now)
        )
        return now - self.last_verified_at > timedelta(hours=24 if near else 72)


class EventPage(BaseModel):
    items: list[EventResponse]
    total: int
    page: int
    page_size: int
    has_more: bool
    date_from: date
    date_to: date
    timezone: Literal["America/New_York"] = "America/New_York"


class ErrorResponse(BaseModel):
    detail: str
