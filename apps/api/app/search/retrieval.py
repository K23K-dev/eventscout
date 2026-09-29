"""Fuse keyword and vector rankings, then keep only events Postgres still lists."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from uuid import UUID

from openai import AsyncOpenAI, OpenAIError
from pinecone import AsyncIndex, DenseVectorQuery, PineconeError

from app.events.models import CATALOG_TIMEZONE, EventFilters, EventResponse
from app.events.repository import EventRepository
from app.indexing.worker import EMBEDDING_MODEL, NAMESPACE

logger = logging.getLogger(__name__)

RRF_K = 60
_DATES = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)?|(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?"
    r"|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
)


@dataclass
class SearchResults:
    hybrid: list[EventResponse]
    keyword: list[EventResponse]
    vector: list[EventResponse]
    # Positions within each full ranking, to explain the fused order.
    keyword_ranks: dict[UUID, int] = field(default_factory=dict)
    vector_ranks: dict[UUID, int] = field(default_factory=dict)
    vector_error: str | None = None


def fuse(*rankings: list[UUID], k: int = RRF_K) -> list[UUID]:
    """Reciprocal rank fusion: each ranking adds 1 / (k + rank) to an event's score."""
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, event_id in enumerate(ranking, start=1):
            scores[event_id] = scores.get(event_id, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda event_id: (-scores[event_id], str(event_id)))


def vector_filter(filters: EventFilters) -> dict[str, Any]:
    """A loose superset of the structured filters; Postgres applies the exact ones."""
    start = datetime.combine(filters.date_from, time.min, CATALOG_TIMEZONE)
    end = datetime.combine(filters.end_date, time.min, CATALOG_TIMEZONE)
    clauses: dict[str, Any] = {
        "starts_at": {"$lt": int(end.timestamp())},
        "ends_at": {"$gte": int(start.timestamp())},
    }
    for name in ("region", "location_kind", "price_status"):
        value = getattr(filters, name)
        if value is not None:
            clauses[name] = {"$eq": value}
    if filters.audience:
        clauses["audience"] = {"$in": [filters.audience.casefold()]}
    return clauses


def distinct(events: list[EventResponse]) -> list[EventResponse]:
    """Keep the best-ranked of repeated listings: one title at one time or one venue."""
    seen: set[tuple[str, object]] = set()
    kept: list[EventResponse] = []
    for event in events:
        # Sessions of a series often put their date in the title.
        title = " ".join(_DATES.sub(" ", _words(event.title)).split())
        start = event.start_date if event.all_day else event.starts_at
        venue = _words((event.venue or "").split(",", 1)[0])
        keys = {(title, start), (title, venue)}
        if keys.isdisjoint(seen):
            kept.append(event)
        seen |= keys
    return kept


def _words(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


async def search_events(
    repository: EventRepository,
    openai: AsyncOpenAI,
    index: AsyncIndex,
    query: str,
    filters: EventFilters,
    *,
    keywords: str | None = None,
    limit: int = 5,
    depth: int = 30,
) -> SearchResults:
    """Rank by keywords and by meaning, fuse, and drop anything that fails a filter.

    Keywords default to the query itself. Without either, the structured filters alone
    apply and events come soonest first.
    """
    text = (query if keywords is None else keywords).strip()
    (keyword_ids, _), (vector_ids, vector_error) = await asyncio.gather(
        # With a query but no keywords, a keyword ranking would only add date order.
        _keyword_ranking(repository, text, filters, depth)
        if text or not query.strip()
        else _no_ranking(),
        _vector_ranking(openai, index, query, filters, depth) if query.strip() else _no_ranking(),
    )
    fused = fuse(keyword_ids, vector_ids)
    current = {event.id: event for event in await repository.get_many(fused, filters)}

    def top(ranking: list[UUID]) -> list[EventResponse]:
        return distinct([current[event_id] for event_id in ranking if event_id in current])[:limit]

    return SearchResults(
        hybrid=top(fused),
        keyword=top(keyword_ids),
        vector=top(vector_ids),
        keyword_ranks={event_id: rank for rank, event_id in enumerate(keyword_ids, start=1)},
        vector_ranks={event_id: rank for rank, event_id in enumerate(vector_ids, start=1)},
        vector_error=vector_error,
    )


async def _keyword_ranking(
    repository: EventRepository, text: str, filters: EventFilters, depth: int
) -> tuple[list[UUID], str | None]:
    ranked = filters.model_copy(
        update={"q": text or None, "sort": "relevance", "page": 1, "page_size": depth}
    )
    return [event.id for event in (await repository.search(ranked)).items], None


async def _vector_ranking(
    openai: AsyncOpenAI, index: AsyncIndex, query: str, filters: EventFilters, depth: int
) -> tuple[list[UUID], str | None]:
    try:
        response = await openai.embeddings.create(model=EMBEDDING_MODEL, input=[query], timeout=10)
        matches = await index.documents.search(
            namespace=NAMESPACE,
            score_by=[DenseVectorQuery(field="embedding", values=response.data[0].embedding)],
            # Pinecone can't match venue substrings, so look deeper and let Postgres filter.
            top_k=depth * 10 if filters.venue else depth,
            filter=vector_filter(filters),
            timeout=5,
        )
    except (OpenAIError, PineconeError, TimeoutError) as exc:
        logger.warning("Vector search unavailable (%s); using keyword results", type(exc).__name__)
        return [], type(exc).__name__
    return [UUID(match.id) for match in matches.matches], None


async def _no_ranking() -> tuple[list[UUID], str | None]:
    return [], None
