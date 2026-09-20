"""Postgres event storage; every write owns one complete transaction."""

import json
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.storage.models import (
    EventContent,
    EventObservation,
    Source,
    SourceInput,
    UpsertResult,
)

_CONTENT_COLUMNS = tuple(EventContent.model_fields)
_COLUMN_NAMES = sql.SQL(", ").join(sql.Identifier(name) for name in _CONTENT_COLUMNS)
_CONTENT_PLACEHOLDERS = sql.SQL(", ").join(sql.Placeholder() for _ in _CONTENT_COLUMNS)
_CONTENT_ASSIGNMENTS = sql.SQL(", ").join(
    sql.SQL("{} = %s").format(sql.Identifier(name)) for name in _CONTENT_COLUMNS
)


class InvalidRunError(ValueError):
    """The ingestion run does not exist, belongs to another source, or is finished."""


class StaleObservationError(ValueError):
    """The observation would regress source freshness or a known source revision."""


class EventStore:
    """Use an idle autocommit connection with dict rows, dedicated to one caller.

    Source record identity is (source_id, external_id, occurrence_key). Adapters must
    choose a stable occurrence_key, never the occurrence's mutable scheduled date.
    Older observations/revisions are rejected atomically. An omitted source revision
    preserves the last known revision. Concurrent callers need separate connections.
    """

    def __init__(self, connection: AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_idle()

    def _require_idle(self) -> None:
        if (
            self._connection.closed
            or not self._connection.autocommit
            or self._connection.info.transaction_status != TransactionStatus.IDLE
        ):
            raise ValueError("EventStore requires an idle autocommit connection")

    async def upsert_source(self, source: SourceInput) -> Source:
        self._require_idle()
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                """
                INSERT INTO eventscout.sources (slug, publisher, name, url)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    publisher = EXCLUDED.publisher, name = EXCLUDED.name,
                    url = EXCLUDED.url, updated_at = clock_timestamp()
                RETURNING id, slug, publisher, name, url
                """,
                (source.slug, source.publisher, source.name, str(source.url)),
            )
            row = await cursor.fetchone()
            assert row is not None
            return Source.model_validate(row)

    async def start_run(self, source_id: UUID) -> UUID:
        self._require_idle()
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                "INSERT INTO eventscout.ingestion_runs (source_id) VALUES (%s) RETURNING id",
                (source_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            return UUID(str(row["id"]))

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: Literal["succeeded", "failed"],
        records_seen: int = 0,
        error: str | None = None,
    ) -> None:
        self._require_idle()
        if status not in {"succeeded", "failed"} or records_seen < 0:
            raise ValueError("a finished run needs a terminal status and nonnegative records_seen")
        if status == "succeeded" and error is not None:
            raise ValueError("a successful run cannot have an error")
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                """
                UPDATE eventscout.ingestion_runs
                SET status = %s, records_seen = %s, error = %s, finished_at = clock_timestamp()
                WHERE id = %s AND status = 'running' RETURNING id
                """,
                (status, records_seen, error, run_id),
            )
            if await cursor.fetchone() is None:
                raise InvalidRunError("run is missing or already finished")

    async def upsert_event(
        self,
        observation: EventObservation,
        *,
        canonical_event_id: UUID | None = None,
        update_content: bool = True,
    ) -> UpsertResult:
        """Store provenance, optionally linking it to an existing canonical event.

        Only the chosen source should update canonical content. Other sources can
        refresh their observations with update_content=False without changing the
        event's content version or creating an indexing job.
        """
        self._require_idle()
        content_hash = observation.content.content_hash()
        values = _content_values(observation.content)
        identity = (
            observation.source_id,
            observation.external_id,
            observation.occurrence_key,
        )
        async with self._connection.transaction():
            if observation.run_id is not None:
                await self._validate_run(observation.run_id, observation.source_id)
            # Row locks cannot protect a record that has not been created yet.
            await self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (json.dumps([str(value) for value in identity]),),
            )
            cursor = await self._connection.execute(
                """
                SELECT id, event_id, observed_at, source_updated_at
                FROM eventscout.source_records
                WHERE source_id = %s AND external_id = %s AND occurrence_key = %s
                FOR UPDATE
                """,
                identity,
            )
            record = await cursor.fetchone()
            if record is not None:
                if canonical_event_id is not None and record["event_id"] != canonical_event_id:
                    raise ValueError("source record is already linked to another canonical event")
                if observation.observed_at < record["observed_at"]:
                    raise StaleObservationError(
                        "observation is older than the last successful check"
                    )
                if (
                    observation.source_updated_at is not None
                    and record["source_updated_at"] is not None
                    and observation.source_updated_at < record["source_updated_at"]
                ):
                    raise StaleObservationError("observation contains an older source revision")
                canonical_event_id = record["event_id"]

            if canonical_event_id is None:
                cursor = await self._connection.execute(
                    sql.SQL(
                        "INSERT INTO eventscout.event_occurrences ({}, content_hash) "
                        "VALUES ({}, %s) RETURNING id, content_version"
                    ).format(_COLUMN_NAMES, _CONTENT_PLACEHOLDERS),
                    (*values, content_hash),
                )
                event = await cursor.fetchone()
                assert event is not None
                changed = True
            else:
                cursor = await self._connection.execute(
                    """
                    SELECT id, content_version, content_hash FROM eventscout.event_occurrences
                    WHERE id = %s FOR UPDATE
                    """,
                    (canonical_event_id,),
                )
                event = await cursor.fetchone()
                if event is None:
                    raise ValueError("canonical event does not exist")
                changed = update_content and event["content_hash"] != content_hash
                if (
                    changed
                    and record is not None
                    and observation.observed_at == record["observed_at"]
                ):
                    raise StaleObservationError("conflicting content at the same observation time")
                if changed:
                    cursor = await self._connection.execute(
                        sql.SQL(
                            "UPDATE eventscout.event_occurrences SET {}, content_hash = %s "
                            "WHERE id = %s RETURNING id, content_version"
                        ).format(_CONTENT_ASSIGNMENTS),
                        (*values, content_hash, event["id"]),
                    )
                    event = await cursor.fetchone()
                    assert event is not None

            if record is None:
                cursor = await self._connection.execute(
                    """
                    INSERT INTO eventscout.source_records
                        (event_id, source_id, external_id, occurrence_key, observed_at,
                         source_updated_at, raw_payload, run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                    """,
                    (
                        event["id"],
                        *identity,
                        observation.observed_at,
                        observation.source_updated_at,
                        Jsonb(observation.raw_payload),
                        observation.run_id,
                    ),
                )
                record = await cursor.fetchone()
                assert record is not None
            else:
                await self._connection.execute(
                    """
                    UPDATE eventscout.source_records
                    SET observed_at = %s, source_updated_at = COALESCE(%s, source_updated_at),
                        raw_payload = %s, run_id = %s, updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (
                        observation.observed_at,
                        observation.source_updated_at,
                        Jsonb(observation.raw_payload),
                        observation.run_id,
                        record["id"],
                    ),
                )
            return UpsertResult(
                event_id=event["id"],
                source_record_id=record["id"],
                content_version=event["content_version"],
                changed=changed,
            )

    async def _validate_run(self, run_id: UUID, source_id: UUID) -> None:
        cursor = await self._connection.execute(
            "SELECT source_id, status FROM eventscout.ingestion_runs WHERE id = %s FOR SHARE",
            (run_id,),
        )
        run = await cursor.fetchone()
        if run is None or run["source_id"] != source_id or run["status"] != "running":
            raise InvalidRunError("run must be active and belong to the observation source")


def _content_values(content: EventContent) -> tuple[Any, ...]:
    fields = content.model_dump(mode="python")
    fields["source_url"] = str(content.source_url)
    fields["registration_url"] = (
        str(content.registration_url) if content.registration_url is not None else None
    )
    return tuple(fields[name] for name in _CONTENT_COLUMNS)
