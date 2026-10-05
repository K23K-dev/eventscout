"""Read-only catalog queries; the caller supplies one consistent transaction."""

from datetime import datetime, time
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection, sql

from app.events.models import CATALOG_TIMEZONE, EventFilters, EventPage, EventResponse
from app.storage.models import EventContent

_PUBLIC_COLUMNS = sql.SQL(", ").join(
    sql.Identifier("e", name) for name in ("id", "content_version", *EventContent.model_fields)
)
# The calendars listing each event, and its AI summary and topics.
_SOURCE_DETAILS = sql.SQL("""
    JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object(
                   'slug', s.slug, 'publisher', s.publisher, 'name', s.name, 'url', s.url
               ) ORDER BY s.slug) AS sources
        FROM eventscout.sources s
        WHERE s.id IN (SELECT source_id FROM eventscout.source_records WHERE event_id = e.id)
    ) provenance ON provenance.sources IS NOT NULL
    LEFT JOIN LATERAL (
        SELECT summary, topics FROM eventscout.event_enrichments
        WHERE content_hash = e.content_hash ORDER BY prompt_version DESC LIMIT 1
    ) enrichment ON true
""")
_DETAIL_COLUMNS = sql.SQL("""
    provenance.sources, enrichment.summary, COALESCE(enrichment.topics, '{}') AS topics,
    (SELECT r.raw_payload->>'image_url' FROM eventscout.source_records r
     WHERE r.event_id = e.id AND r.raw_payload->>'image_url' IS NOT NULL
     ORDER BY r.observed_at DESC LIMIT 1) AS image_url
""")
_START_TIME = sql.SQL("""
    CASE WHEN e.all_day THEN e.start_date::timestamp AT TIME ZONE e.timezone
         ELSE e.starts_at END
""")


class EventRepository:
    def __init__(self, connection: AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection

    async def search(self, filters: EventFilters) -> EventPage:
        where, parameters = self._search_conditions(filters)
        cursor = await self._connection.execute(
            sql.SQL("SELECT count(*) AS total FROM eventscout.event_occurrences e WHERE {}").format(
                where
            ),
            parameters,
        )
        count = await cursor.fetchone()
        assert count is not None
        total = int(count["total"])
        offset = (filters.page - 1) * filters.page_size
        items: list[EventResponse] = []
        if offset < total:
            rank = (
                sql.SQL(
                    "ts_rank_cd(e.search_document, "
                    "websearch_to_tsquery('pg_catalog.english', %(q)s), 32) DESC, "
                )
                if filters.q
                else sql.SQL("")
            )
            # Soonest first means what starts in the window, then what was already running
            # (months-long exhibitions would otherwise fill the first pages).
            cursor = await self._connection.execute(
                sql.SQL("""
                    SELECT {}, {}
                    FROM eventscout.event_occurrences e {}
                    WHERE {} ORDER BY {} ({} < %(window_start)s) ASC, {} ASC, e.id ASC
                    LIMIT %(limit)s OFFSET %(offset)s
                """).format(
                    _PUBLIC_COLUMNS,
                    _DETAIL_COLUMNS,
                    _SOURCE_DETAILS,
                    where,
                    rank,
                    _START_TIME,
                    _START_TIME,
                ),
                {**parameters, "limit": filters.page_size, "offset": offset},
            )
            items = [EventResponse.model_validate(row) for row in await cursor.fetchall()]
        return EventPage(
            items=items,
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            has_more=offset + len(items) < total,
            date_from=filters.date_from,
            date_to=filters.end_date,
        )

    async def get(self, event_id: UUID) -> EventResponse | None:
        cursor = await self._connection.execute(
            sql.SQL("""
                SELECT {}, {}
                FROM eventscout.event_occurrences e {} WHERE e.id = (
                    SELECT COALESCE(merged_into, id) FROM eventscout.event_occurrences
                    WHERE id = %(id)s
                )
            """).format(_PUBLIC_COLUMNS, _DETAIL_COLUMNS, _SOURCE_DETAILS),
            {"id": event_id},
        )
        row = await cursor.fetchone()
        return EventResponse.model_validate(row) if row is not None else None

    async def get_by_ids(self, event_ids: list[UUID]) -> list[EventResponse]:
        """Events in the given order, whatever their date or status, as earlier turns showed."""
        if not event_ids:
            return []
        cursor = await self._connection.execute(
            sql.SQL("""
                SELECT {}, {}
                FROM eventscout.event_occurrences e {} WHERE e.id = ANY(%(ids)s)
                ORDER BY array_position(%(ids)s::uuid[], e.id)
            """).format(_PUBLIC_COLUMNS, _DETAIL_COLUMNS, _SOURCE_DETAILS),
            {"ids": event_ids},
        )
        return [EventResponse.model_validate(row) for row in await cursor.fetchall()]

    async def get_many(self, event_ids: list[UUID], filters: EventFilters) -> list[EventResponse]:
        """Listed events among the given IDs that pass every filter except the keywords."""
        if not event_ids:
            return []
        where, parameters = self._search_conditions(filters.model_copy(update={"q": None}))
        cursor = await self._connection.execute(
            sql.SQL("""
                SELECT {}, {}
                FROM eventscout.event_occurrences e {} WHERE e.id = ANY(%(ids)s) AND {}
            """).format(_PUBLIC_COLUMNS, _DETAIL_COLUMNS, _SOURCE_DETAILS, where),
            {**parameters, "ids": event_ids},
        )
        return [EventResponse.model_validate(row) for row in await cursor.fetchall()]

    def _search_conditions(self, filters: EventFilters) -> tuple[sql.Composed, dict[str, Any]]:
        parameters: dict[str, Any] = {
            "window_start": datetime.combine(filters.date_from, time.min, CATALOG_TIMEZONE),
            "window_end": datetime.combine(filters.end_date, time.min, CATALOG_TIMEZONE),
        }
        clauses: list[sql.Composable] = [
            sql.SQL("e.merged_into IS NULL AND e.status = 'scheduled'"),
            sql.SQL("""
                CASE WHEN e.all_day THEN
                    (e.start_date::timestamp AT TIME ZONE e.timezone) < %(window_end)s AND
                    (COALESCE(e.end_date, e.start_date + 1)::timestamp AT TIME ZONE e.timezone)
                        > %(window_start)s
                ELSE e.starts_at < %(window_end)s AND
                    COALESCE(e.ends_at > %(window_start)s, e.starts_at >= %(window_start)s)
                END
            """),
        ]
        if filters.q:
            parameters["q"] = filters.q
            clauses.append(
                sql.SQL("e.search_document @@ websearch_to_tsquery('pg_catalog.english', %(q)s)")
            )
        for name in ("region", "location_kind", "price_status"):
            value = getattr(filters, name)
            if value is not None:
                parameters[name] = value
                clauses.append(
                    sql.SQL("{} = {}").format(sql.Identifier("e", name), sql.Placeholder(name))
                )
        if filters.topic:
            parameters["topic"] = filters.topic
            clauses.append(
                sql.SQL("""
                    (SELECT topics FROM eventscout.event_enrichments
                     WHERE content_hash = e.content_hash ORDER BY prompt_version DESC LIMIT 1)
                    @> ARRAY[%(topic)s]::text[]
                """)
            )
        if filters.venue:
            parameters["venue"] = filters.venue
            clauses.append(sql.SQL("strpos(lower(e.venue), lower(%(venue)s)) > 0"))
        return sql.SQL(" AND ").join(clauses), parameters
