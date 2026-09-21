"""Persist bounded HTTP caches and select omitted events worth checking again."""

import gzip
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.ingestion.http import CacheEntry, ResponseCache
from app.ingestion.records import ParsedEvent
from app.storage.models import EventContent


async def load_caches(
    connection: AsyncConnection[dict[str, Any]], source_ids: dict[str, UUID]
) -> dict[str, ResponseCache]:
    caches = {slug: ResponseCache() for slug in source_ids}
    cursor = await connection.execute(
        """SELECT s.slug, c.* FROM eventscout.feed_cache c
           JOIN eventscout.sources s ON s.id = c.source_id WHERE s.id = ANY(%s)""",
        (list(source_ids.values()),),
    )
    for row in await cursor.fetchall():
        try:
            body = gzip.decompress(row["body"])
        except (OSError, EOFError):
            continue  # A damaged cache is a miss, never a reason to lose the catalog.
        caches[row["slug"]].entries[row["url"]] = CacheEntry(
            body, row["etag"], row["last_modified"], row["checked_at"]
        )
    for cache in caches.values():
        cache.prune()
    return caches


async def save_caches(
    connection: AsyncConnection[dict[str, Any]],
    source_ids: dict[str, UUID],
    caches: dict[str, ResponseCache],
) -> None:
    async with connection.transaction():
        for slug, cache in caches.items():
            source_id = source_ids[slug]
            await connection.execute(
                "DELETE FROM eventscout.feed_cache WHERE source_id = %s", (source_id,)
            )
            if cache.entries:
                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        """INSERT INTO eventscout.feed_cache
                           (source_id, url, body, etag, last_modified, checked_at)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        [
                            (
                                source_id,
                                url,
                                gzip.compress(entry.body, compresslevel=3),
                                entry.etag,
                                entry.last_modified,
                                entry.checked_at,
                            )
                            for url, entry in cache.entries.items()
                        ],
                    )


@dataclass
class Recheck:
    record_id: UUID
    source: str
    event: ParsedEvent


async def omitted_events(
    connection: AsyncConnection[dict[str, Any]],
    source_ids: dict[str, UUID],
    seen: set[tuple[str, str]],
    now: datetime,
) -> tuple[list[Recheck], dict[str, int]]:
    cursor = await connection.execute(
        """SELECT r.*, s.slug, to_jsonb(e) AS canonical_content
           FROM eventscout.source_records r JOIN eventscout.sources s ON s.id = r.source_id
           JOIN eventscout.event_occurrences e ON e.id = r.event_id
           WHERE s.id = ANY(%(sources)s) AND e.merged_into IS NULL AND e.status = 'scheduled'
             AND CASE WHEN e.all_day THEN
               (e.start_date::timestamp AT TIME ZONE e.timezone) < %(now)s + interval '7 days'
               AND (COALESCE(e.end_date, e.start_date + 1)::timestamp AT TIME ZONE e.timezone)
                   > %(now)s
             ELSE e.starts_at < %(now)s + interval '7 days'
               AND COALESCE(e.ends_at > %(now)s, e.starts_at >= %(now)s) END
           ORDER BY r.last_recheck_at NULLS FIRST, r.observed_at, r.id""",
        {"sources": list(source_ids.values()), "now": now},
    )
    selected: list[Recheck] = []
    counts = dict.fromkeys(source_ids, 0)
    deferred = dict.fromkeys(source_ids, 0)
    for row in await cursor.fetchall():
        slug = row["slug"]
        if (slug, row["external_id"]) in seen:
            continue
        if counts[slug] >= 10 or (
            row["last_recheck_at"] is not None
            and row["last_recheck_at"] >= now - timedelta(hours=6)
        ):
            deferred[slug] += 1
            continue
        counts[slug] += 1
        content = row["content"] or {
            name: row["canonical_content"][name] for name in EventContent.model_fields
        }
        selected.append(
            Recheck(
                row["id"],
                slug,
                ParsedEvent(
                    row["external_id"],
                    EventContent.model_validate(content),
                    row["source_updated_at"],
                    row["raw_payload"],
                ),
            )
        )
    return selected, {slug: count for slug, count in deferred.items() if count}
