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
_SOURCE_DETAILS = sql.SQL("""
    JOIN LATERAL (
        SELECT max(r.observed_at) AS last_observed_at,
               jsonb_agg(jsonb_build_object(
                   'slug', s.slug, 'publisher', s.publisher, 'name', s.name,
                   'url', s.url, 'last_observed_at', r.observed_at
               ) ORDER BY s.slug) AS sources
        FROM (
            SELECT source_id, max(observed_at) AS observed_at
            FROM eventscout.source_records WHERE event_id = e.id GROUP BY source_id
        ) r JOIN eventscout.sources s ON s.id = r.source_id
        WHERE s.slug = ANY(%(enabled_sources)s)
    ) provenance ON provenance.last_observed_at IS NOT NULL
""")
_ENABLED_EVENT = sql.SQL("""
    EXISTS (
        SELECT 1 FROM eventscout.source_records r
        JOIN eventscout.sources s ON s.id = r.source_id
        WHERE r.event_id = e.id AND s.slug = ANY(%(enabled_sources)s)
    )
""")
_START_TIME = sql.SQL("""
    CASE WHEN e.all_day THEN e.start_date::timestamp AT TIME ZONE e.timezone
         ELSE e.starts_at END
""")


class EventRepository:
    def __init__(
        self, connection: AsyncConnection[dict[str, Any]], enabled_sources: tuple[str, ...]
    ) -> None:
        self._connection = connection
        self._enabled_sources = enabled_sources

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
                if filters.q and filters.sort == "relevance"
                else sql.SQL("")
            )
            cursor = await self._connection.execute(
                sql.SQL("""
                    SELECT {}, provenance.last_observed_at, provenance.sources
                    FROM eventscout.event_occurrences e {}
                    WHERE {} ORDER BY {} {} ASC, e.id ASC
                    LIMIT %(limit)s OFFSET %(offset)s
                """).format(_PUBLIC_COLUMNS, _SOURCE_DETAILS, where, rank, _START_TIME),
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
                SELECT {}, provenance.last_observed_at, provenance.sources
                FROM eventscout.event_occurrences e {} WHERE e.id = %(id)s
            """).format(_PUBLIC_COLUMNS, _SOURCE_DETAILS),
            {"id": event_id, "enabled_sources": list(self._enabled_sources)},
        )
        row = await cursor.fetchone()
        return EventResponse.model_validate(row) if row is not None else None

    def _search_conditions(self, filters: EventFilters) -> tuple[sql.Composed, dict[str, Any]]:
        parameters: dict[str, Any] = {
            "enabled_sources": list(self._enabled_sources),
            "window_start": datetime.combine(filters.date_from, time.min, CATALOG_TIMEZONE),
            "window_end": datetime.combine(filters.end_date, time.min, CATALOG_TIMEZONE),
        }
        clauses: list[sql.Composable] = [
            sql.SQL("e.status = 'scheduled'"),
            _ENABLED_EVENT,
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
        if filters.venue:
            parameters["venue"] = filters.venue
            clauses.append(sql.SQL("strpos(lower(e.venue), lower(%(venue)s)) > 0"))
        if filters.audience:
            parameters["audience"] = filters.audience
            clauses.append(
                sql.SQL("""
                    EXISTS (SELECT 1 FROM unnest(e.audience) AS labels(label)
                            WHERE lower(label) = lower(%(audience)s))
                """)
            )
        return sql.SQL(" AND ").join(clauses), parameters
