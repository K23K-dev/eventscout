"""The public catalog contract and its validated search parameters."""

from datetime import date, datetime, timedelta
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    q: str | None = Field(default=None, max_length=200)
    # Calendar dates in America/New_York; date_to is exclusive and defaults to 30 days later.
    date_from: date = Field(
        default_factory=lambda: datetime.now(CATALOG_TIMEZONE).date(),
        ge=date(1900, 1, 1),
        le=date(2100, 1, 1),
    )
    date_to: date | None = Field(default=None, ge=date(1900, 1, 1), le=date(2100, 4, 1))
    region: Literal["gt", "atlanta"] | None = None
    location_kind: Literal["in_person", "online", "hybrid", "unknown"] | None = None
    price_status: Literal["free", "paid", "conditional", "unknown"] | None = None
    venue: str | None = Field(default=None, max_length=200)
    topic: Topic | None = None
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
    sources: list[EventSource]
    summary: str | None = Field(default=None, description="One-line summary written by enrichment.")
    topics: list[str] = Field(default_factory=list, description="1-3 enrichment Topic values.")
    image_url: str | None = Field(default=None, description="A picture the calendar published.")


class EventPage(BaseModel):
    items: list[EventResponse]
    total: int
    page: int
    page_size: int
    has_more: bool
    date_from: date
    date_to: date
    timezone: Literal["America/New_York"] = "America/New_York"
