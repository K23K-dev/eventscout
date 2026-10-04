"""Validated source observations and canonical event content."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    field_validator,
    model_validator,
)

_CANCELLED_TITLE = re.compile(r"^[\s*\[(]*(?:cancelled|canceled)\b", re.I)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )


class SourceInput(_FrozenModel):
    slug: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: HttpUrl


class Source(SourceInput):
    id: UUID


class EventContent(_FrozenModel):
    title: str = Field(min_length=1)
    description: str = ""
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    all_day: bool = False
    start_date: date | None = None
    end_date: date | None = None
    timezone: str = "America/New_York"
    venue: str | None = None
    location_kind: Literal["in_person", "online", "hybrid", "unknown"] = "unknown"
    region: Literal["gt", "atlanta"] = "atlanta"
    price_status: Literal["free", "paid", "conditional", "unknown"] = "unknown"
    price_details: str | None = None
    audience: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_url: HttpUrl
    registration_url: HttpUrl | None = None
    status: Literal["scheduled", "cancelled"] = "scheduled"

    @model_validator(mode="before")
    @classmethod
    def cancelled_title(cls, data: Any) -> Any:
        # Publishers often cancel an event only by renaming it ("CANCELLED: ...").
        if isinstance(data, dict) and _CANCELLED_TITLE.match(str(data.get("title", ""))):
            return {**data, "status": "cancelled"}
        return data

    @field_validator("starts_at", "ends_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone") from exc
        return value

    @field_validator("audience", "tags")
    @classmethod
    def normalize_labels(cls, value: list[str]) -> list[str]:
        return sorted({label.strip() for label in value if label.strip()})

    @field_validator("venue", "price_details")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def validate_event_time(self) -> Self:
        if self.all_day:
            if self.start_date is None or self.starts_at is not None or self.ends_at is not None:
                raise ValueError("all-day events need start_date and cannot have timestamps")
            if self.end_date is not None and self.end_date <= self.start_date:
                raise ValueError("end_date is exclusive and must be after start_date")
        else:
            if self.start_date is not None or self.end_date is not None:
                raise ValueError("timed events cannot have all-day dates")
            if self.ends_at is not None and (
                self.starts_at is None or self.ends_at <= self.starts_at
            ):
                raise ValueError("ends_at requires starts_at and must be later")
        return self

    def content_hash(self) -> str:
        """Hash normalized content only, independently of fetch metadata."""
        content = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class EventObservation(_FrozenModel):
    source_id: UUID
    external_id: str = Field(min_length=1)
    occurrence_key: str = ""
    content: EventContent
    observed_at: AwareDatetime
    source_updated_at: AwareDatetime | None = None
    raw_payload: JsonValue = Field(default_factory=dict)

    @field_validator("observed_at", "source_updated_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class UpsertResult(_FrozenModel):
    event_id: UUID
    source_record_id: UUID
    content_version: int = Field(ge=1)
    changed: bool
