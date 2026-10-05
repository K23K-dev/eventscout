"""Keep the Pinecone index in step with the catalog by draining its job queue."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI, OpenAIError
from pinecone import AsyncIndex, AsyncPinecone, PineconeError
from psycopg import AsyncConnection, sql

from app.database import connect_database
from app.settings import Settings
from app.storage.models import EventContent

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
NAMESPACE = "events"
_BATCH_SIZE = 50
_MAX_ATTEMPTS = 5
_DESCRIPTION_LIMIT = 6000
_CONTENT_COLUMNS = sql.SQL(", ").join(
    sql.Identifier("e", name) for name in EventContent.model_fields
)


@dataclass
class IndexReport:
    requeued: int = 0
    claimed: int = 0
    embedded: int = 0
    removed: int = 0
    expired: int = 0
    pruned: int = 0
    superseded: int = 0
    tokens: int = 0
    errors: list[str] = field(default_factory=list)


def embedding_text(
    content: EventContent, summary: str | None = None, topics: Sequence[str] = ()
) -> str:
    """Describe what an event is about; dates, prices, and formats are filtered instead."""
    lines = [content.title]
    if summary:
        lines.append(f"Summary: {summary}")
    if topics:
        lines.append(f"Kind: {', '.join(topics)}")
    if content.venue:
        lines.append(f"Venue: {content.venue}")
    if content.tags:
        lines.append(f"Topics: {', '.join(content.tags)}")
    if content.audience:
        lines.append(f"Audience: {', '.join(content.audience)}")
    if content.description:
        lines.append(content.description[:_DESCRIPTION_LIMIT])
    return "\n".join(lines)


def event_bounds(content: EventContent) -> tuple[datetime, datetime] | None:
    """When an occurrence starts and stops being listed; all-day end dates are exclusive."""
    if content.start_date is not None:
        zone = ZoneInfo(content.timezone)
        return (
            datetime.combine(content.start_date, time.min, zone),
            datetime.combine(
                content.end_date or content.start_date + timedelta(days=1), time.min, zone
            ),
        )
    if content.starts_at is None:
        return None
    return content.starts_at, content.ends_at or content.starts_at


def vector_metadata(content: EventContent, bounds: tuple[datetime, datetime]) -> dict[str, Any]:
    """Coarse filters for dense search; Postgres rechecks every hard filter afterwards."""
    return {
        "starts_at": int(bounds[0].timestamp()),
        "ends_at": int(bounds[1].timestamp()),
        "region": content.region,
        "price_status": content.price_status,
        "location_kind": content.location_kind,
    }


async def requeue(
    connection: AsyncConnection[dict[str, Any]], content_hashes: list[str] | None = None
) -> int:
    """Queue events' current versions again, all or those with the given content hashes."""
    cursor = await connection.execute(
        """UPDATE eventscout.index_jobs j SET
               status = 'pending', attempts = 0, available_at = clock_timestamp(),
               locked_at = NULL, finished_at = NULL, last_error = NULL
           FROM eventscout.event_occurrences e
           WHERE j.event_id = e.id AND j.content_version = e.content_version
             AND j.status IN ('succeeded', 'failed')
             AND (%(hashes)s::text[] IS NULL OR e.content_hash = ANY(%(hashes)s::text[]))""",
        {"hashes": content_hashes},
    )
    return cursor.rowcount


async def run_indexing(settings: Settings, *, reindex: bool = False) -> IndexReport:
    """Create the index on first use, then embed, remove, or skip queued events.

    With reindex, every event's current version is queued again first, to apply a change
    in how events are embedded or described.
    """
    assert settings.openai_api_key is not None and settings.pinecone_api_key is not None
    async with (
        connect_database(settings) as connection,
        AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()) as openai,
        AsyncPinecone(api_key=settings.pinecone_api_key.get_secret_value()) as pinecone,
    ):
        if not await pinecone.indexes.exists(settings.pinecone_index):
            logger.info("Creating Pinecone index %s", settings.pinecone_index)
            await pinecone.indexes.create(
                name=settings.pinecone_index,
                schema={
                    "fields": {
                        "embedding": {"type": "dense_vector", "dimension": 1536, "metric": "cosine"}
                    }
                },
            )
        requeued = await requeue(connection) if reindex else 0
        async with await pinecone.index(settings.pinecone_index) as index:
            report = await _drain(connection, openai, index)
            report.requeued = requeued
            return report


async def _drain(
    connection: AsyncConnection[dict[str, Any]], openai: AsyncOpenAI, index: AsyncIndex
) -> IndexReport:
    report = IndexReport()
    # A crashed run leaves its claimed batch in processing; return it after a grace period.
    await connection.execute(
        """UPDATE eventscout.index_jobs SET status = 'pending', locked_at = NULL
           WHERE status = 'processing' AND locked_at < clock_timestamp() - interval '15 minutes'"""
    )
    while True:
        cursor = await connection.execute(
            """UPDATE eventscout.index_jobs SET status = 'processing', locked_at = clock_timestamp()
               WHERE id IN (
                   SELECT id FROM eventscout.index_jobs
                   WHERE status = 'pending' AND available_at <= clock_timestamp()
                   ORDER BY available_at, created_at LIMIT %s FOR UPDATE SKIP LOCKED
               )
               RETURNING id, event_id, content_version, operation""",
            (_BATCH_SIZE,),
        )
        jobs = await cursor.fetchall()
        if not jobs:
            break
        report.claimed += len(jobs)
        try:
            await _apply(connection, openai, index, jobs, report)
        except (OpenAIError, PineconeError, TimeoutError) as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            report.errors.append(error)
            await _retry_later(connection, [job["id"] for job in jobs], error)
            break  # A provider outage would fail every batch; the next run retries.
        if report.claimed % 1000 < len(jobs):
            logger.info("Processed %s index jobs", report.claimed)
    # Events leave the catalog once they end, without a content change to queue a job.
    try:
        response = await index.documents.delete(
            namespace=NAMESPACE, filter={"ends_at": {"$lte": int(datetime.now(UTC).timestamp())}}
        )
        report.pruned = response.matched_records or 0
    except (PineconeError, TimeoutError) as exc:
        report.errors.append(f"{type(exc).__name__}: {exc}"[:500])
    return report


async def _apply(
    connection: AsyncConnection[dict[str, Any]],
    openai: AsyncOpenAI,
    index: AsyncIndex,
    jobs: list[dict[str, Any]],
    report: IndexReport,
) -> None:
    cursor = await connection.execute(
        sql.SQL(
            "SELECT e.id, e.content_version, e.merged_into, {}, x.summary, x.topics "
            "FROM eventscout.event_occurrences e LEFT JOIN LATERAL ("
            "  SELECT summary, topics FROM eventscout.event_enrichments"
            "  WHERE content_hash = e.content_hash ORDER BY prompt_version DESC LIMIT 1"
            ") x ON true WHERE e.id = ANY(%s)"
        ).format(_CONTENT_COLUMNS),
        ([job["event_id"] for job in jobs],),
    )
    events = {row["id"]: row for row in await cursor.fetchall()}
    now = datetime.now(UTC)
    upserts: list[tuple[UUID, EventContent, tuple[datetime, datetime], str]] = []
    removed: list[str] = []
    expired: list[str] = []
    superseded = 0
    for job in jobs:
        event = events[job["event_id"]]
        if event["content_version"] != job["content_version"]:
            superseded += 1  # The newer version has its own job.
            continue
        content = EventContent.model_validate(
            {name: event[name] for name in EventContent.model_fields}
        )
        bounds = event_bounds(content)
        if (
            job["operation"] == "delete"
            or event["merged_into"] is not None
            or content.status == "cancelled"
        ):
            removed.append(str(event["id"]))
        elif bounds is None or bounds[1] <= now:
            expired.append(str(event["id"]))
        else:
            text = embedding_text(content, event["summary"], event["topics"] or ())
            upserts.append((event["id"], content, bounds, text))
    if upserts:
        response = await openai.embeddings.create(
            model=EMBEDDING_MODEL, input=[text for *_, text in upserts]
        )
        vectors = sorted(response.data, key=lambda item: item.index)
        await index.documents.upsert(
            namespace=NAMESPACE,
            documents=[
                {"_id": str(event_id), "embedding": vector.embedding}
                | vector_metadata(content, bounds)
                for (event_id, content, bounds, _), vector in zip(upserts, vectors, strict=True)
            ],
        )
        report.tokens += response.usage.total_tokens
    if removed or expired:
        await index.documents.delete(namespace=NAMESPACE, ids=removed + expired)
    await connection.execute(
        """UPDATE eventscout.index_jobs
           SET status = 'succeeded', finished_at = clock_timestamp(), last_error = NULL
           WHERE id = ANY(%s)""",
        ([job["id"] for job in jobs],),
    )
    report.embedded += len(upserts)
    report.removed += len(removed)
    report.expired += len(expired)
    report.superseded += superseded


async def _retry_later(
    connection: AsyncConnection[dict[str, Any]], job_ids: list[UUID], error: str
) -> None:
    """Back off exponentially; a job that keeps failing is parked as failed for inspection."""
    await connection.execute(
        """UPDATE eventscout.index_jobs SET
               attempts = attempts + 1,
               status = CASE WHEN attempts + 1 >= %(max)s THEN 'failed' ELSE 'pending' END,
               finished_at = CASE WHEN attempts + 1 >= %(max)s THEN clock_timestamp() END,
               locked_at = NULL,
               available_at = clock_timestamp() + make_interval(mins => (2 ^ (attempts + 1))::int),
               last_error = %(error)s
           WHERE id = ANY(%(ids)s)""",
        {"max": _MAX_ATTEMPTS, "error": error, "ids": job_ids},
    )
