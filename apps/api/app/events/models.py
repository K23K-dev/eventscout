"""The public catalog contract and its validated search parameters."""

from datetime import date, datetime, timedelta
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.storage.models import EventContent, SourceInput

CATALOG_TIMEZONE = ZoneInfo("America/New_York")


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
    audience: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Exact source-provided audience label, case-insensitive; no inferred eligibility."
        ),
    )
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
    last_observed_at: AwareDatetime


class EventResponse(EventContent):
    id: UUID
    content_version: int
    last_observed_at: AwareDatetime = Field(
        description="Most recent observation across enabled sources, not an event content change."
    )
    sources: list[EventSource]


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
