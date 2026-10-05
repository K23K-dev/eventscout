"""The chat API contract: requests, streamed answers, and stored conversations."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.events.models import EventResponse


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=500)
    conversation_id: UUID | None = Field(
        default=None, description="Omit to start a new conversation."
    )


class TurnAnswer(BaseModel):
    reply: str | None = Field(default=None, description="Cites cards inline as [n].")
    cards: list[EventResponse] = Field(default_factory=list)
    clarification: str | None = Field(default=None, description="A question instead of results.")
    note: str | None = Field(default=None, description="Why a written reply is missing.")


class TurnView(TurnAnswer):
    id: UUID
    message: str
    status: Literal["running", "done", "failed"]
    created_at: AwareDatetime


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    updated_at: AwareDatetime


class ConversationDetail(ConversationSummary):
    turns: list[TurnView]
