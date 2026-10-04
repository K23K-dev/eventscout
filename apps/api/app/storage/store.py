"""Postgres event storage; every write owns one complete transaction."""

import json
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection, sql
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

    async def upsert_source(self, source: SourceInput) -> Source:
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

    async def upsert_group(
        self,
        observations: list[EventObservation],
        *,
        canonical_event_id: UUID | None = None,
        merge_ids: tuple[UUID, ...] = (),
        update_content: bool = True,
    ) -> list[UpsertResult]:
        """Save a reconciled group and its aliases in one transaction.

        The first observation supplies canonical content. Every member preserves
        its own content and freshness; a failed member rolls back the entire group.
        The caller must establish duplicate evidence before requesting a merge.
        """
        identities = [
            (observation.source_id, observation.external_id, observation.occurrence_key)
            for observation in observations
        ]
        contents = [observation.content.model_dump(mode="json") for observation in observations]
        content_hash = observations[0].content.content_hash()
        values = _content_values(observations[0].content)
        async with self._connection.transaction():
            # Lock identities even when their source records have not been created yet.
            for identity in sorted(identities):
                await self._connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (json.dumps([str(value) for value in identity]),),
                )
            records: list[dict[str, Any] | None] = []
            for identity in identities:
                cursor = await self._connection.execute(
                    """
                    SELECT id, event_id, observed_at, source_updated_at, content
                    FROM eventscout.source_records
                    WHERE source_id = %s AND external_id = %s AND occurrence_key = %s
                    """,
                    identity,
                )
                records.append(await cursor.fetchone())
            known_ids = {record["event_id"] for record in records if record is not None}
            if canonical_event_id is None and len(known_ids) == 1:
                canonical_event_id = next(iter(known_ids))
            targets = set(merge_ids)
            if targets and (canonical_event_id is None or canonical_event_id in targets):
                raise ValueError("a merge needs a separate canonical event")
            if canonical_event_id is not None:
                targets.add(canonical_event_id)
            if known_ids - targets:
                raise ValueError("source record is already linked to another canonical event")
            # Event locks also serialize movement of records from other source identities.
            cursor = await self._connection.execute(
                """
                SELECT id, content_version, content_hash, merged_into
                FROM eventscout.event_occurrences
                WHERE id = ANY(%s) OR merged_into = ANY(%s)
                ORDER BY id FOR UPDATE
                """,
                (list(targets), list(merge_ids)),
            )
            locked = {row["id"]: row for row in await cursor.fetchall()}
            if (
                canonical_event_id is not None
                and locked[canonical_event_id]["merged_into"] is not None
            ):
                raise ValueError("canonical event was already merged; reconcile again")
            if any(
                locked[event_id]["merged_into"] not in {None, canonical_event_id}
                for event_id in merge_ids
            ):
                raise ValueError("duplicate event was already merged elsewhere; reconcile again")
            merge_ids = tuple(
                event_id for event_id in merge_ids if locked[event_id]["merged_into"] is None
            )
            for observation, content, record in zip(observations, contents, records, strict=True):
                if record is None:
                    continue
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
                if (
                    observation.observed_at == record["observed_at"]
                    and record["content"] is not None
                    and record["content"] != content
                ):
                    raise StaleObservationError("conflicting content at the same observation time")

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
                event = locked[canonical_event_id]
                changed = update_content and event["content_hash"] != content_hash
                if (
                    changed
                    and records[0] is not None
                    and records[0]["content"] is None
                    and observations[0].observed_at == records[0]["observed_at"]
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

            if update_content:
                await self._connection.execute(
                    """UPDATE eventscout.event_occurrences
                       SET last_verified_at = GREATEST(last_verified_at, %s) WHERE id = %s""",
                    (observations[0].observed_at, event["id"]),
                )
            if merge_ids:
                await self._connection.execute(
                    """
                    UPDATE eventscout.source_records SET event_id = %s
                    WHERE event_id = ANY(%s)
                    """,
                    (event["id"], list(merge_ids)),
                )
                await self._connection.execute(
                    """
                    UPDATE eventscout.event_occurrences SET merged_into = %s
                    WHERE id = ANY(%s) OR merged_into = ANY(%s)
                    """,
                    (event["id"], list(merge_ids), list(merge_ids)),
                )
            results: list[UpsertResult] = []
            for index, (observation, identity, content, record) in enumerate(
                zip(observations, identities, contents, records, strict=True)
            ):
                metadata = (
                    observation.observed_at,
                    observation.source_updated_at,
                    Jsonb(observation.raw_payload),
                    Jsonb(content),
                )
                if record is None:
                    cursor = await self._connection.execute(
                        """
                        INSERT INTO eventscout.source_records
                            (event_id, source_id, external_id, occurrence_key, observed_at,
                             source_updated_at, raw_payload, content)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                        """,
                        (event["id"], *identity, *metadata),
                    )
                    record = await cursor.fetchone()
                    assert record is not None
                else:
                    await self._connection.execute(
                        """
                        UPDATE eventscout.source_records
                        SET observed_at = %s, source_updated_at = COALESCE(%s, source_updated_at),
                            raw_payload = %s, content = %s,
                            updated_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (*metadata, record["id"]),
                    )
                results.append(
                    UpsertResult(
                        event_id=event["id"],
                        source_record_id=record["id"],
                        content_version=event["content_version"],
                        changed=changed and index == 0,
                    )
                )
            return results


def _content_values(content: EventContent) -> tuple[Any, ...]:
    fields = content.model_dump(mode="python")
    fields["source_url"] = str(content.source_url)
    fields["registration_url"] = (
        str(content.registration_url) if content.registration_url is not None else None
    )
    return tuple(fields[name] for name in _CONTENT_COLUMNS)
