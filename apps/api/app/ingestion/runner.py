"""Collect public calendars, reconcile exact matches, and persist bounded imports."""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID

import httpx
from psycopg import AsyncConnection
from pydantic import JsonValue

from app.database import connect_database
from app.ingestion.freshness import load_caches, omitted_events, save_caches
from app.ingestion.http import ResponseCache, use_cache
from app.ingestion.matching import CatalogRecord, EventGroup, Observation, group_events
from app.ingestion.recheck import recheck_event
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection, in_window
from app.ingestion.sources import SOURCES, CalendarSource
from app.settings import Settings
from app.storage.models import EventContent, EventObservation
from app.storage.store import EventStore, StaleObservationError

logger = logging.getLogger(__name__)


@dataclass
class SourceReport:
    source: str
    publisher: str
    run_id: str | None = None
    requests: int = 0
    not_modified: int = 0
    records_seen: int = 0
    eligible_records: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    linked: int = 0
    merged: int = 0
    recheck_attempts: int = 0
    rechecked: int = 0
    unconfirmed: int = 0
    recheck_deferred: int = 0
    coverage_complete: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    dry_run: bool
    window_start: str
    window_end: str
    sources: list[SourceReport] = field(default_factory=list)
    unique_events: int = 0
    duplicate_source_records: int = 0
    catalog: dict[str, int] = field(default_factory=dict)
    preview: list[dict[str, object]] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(source.issues for source in self.sources)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "EventScout/0.1 (public calendar event discovery)"},
        timeout=httpx.Timeout(25),
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(retries=2, limits=httpx.Limits(max_connections=8)),
    )


async def _collect(
    sources: list[CalendarSource],
    start: datetime,
    end: datetime,
    report: ImportReport,
    *,
    connection: AsyncConnection[dict[str, Any]] | None = None,
    source_ids: dict[str, UUID] | None = None,
) -> list[Observation]:
    caches = (
        await load_caches(connection, source_ids)
        if connection is not None and source_ids is not None
        else {source.config.slug: ResponseCache() for source in sources}
    )
    async with _client() as client:

        async def collect_source(source: CalendarSource) -> SourceCollection:
            with use_cache(caches[source.config.slug]):
                return await source.collect(client, window_start=start, window_end=end)

        collections = await asyncio.gather(
            *(collect_source(source) for source in sources),
            return_exceptions=True,
        )
    observations: list[Observation] = []
    for source, collection, summary in zip(sources, collections, report.sources, strict=True):
        if isinstance(collection, BaseException):
            summary.issues.append(f"Collection failed ({type(collection).__name__})")
            continue
        summary.requests = collection.requests
        summary.coverage_complete = collection.coverage_complete
        summary.records_seen = collection.records_seen
        summary.issues.extend(
            f"{issue.external_id or 'feed'}: {issue.message}" for issue in collection.issues
        )
        summary.warnings.extend(collection.warnings)
        identities: dict[str, list[ParsedEvent]] = {}
        for event in collection.events:
            identities.setdefault(event.external_id, []).append(event)
        events: list[ParsedEvent] = []
        for external_id, versions in identities.items():
            revisions = [event.source_updated_at for event in versions]
            if all(revision is not None for revision in revisions):
                latest = max(revision for revision in revisions if revision is not None)
                newest = [event for event in versions if event.source_updated_at == latest]
            else:
                newest = versions
            if len({event.content.content_hash() for event in newest}) != 1:
                summary.issues.append(f"{external_id}: conflicting source observations")
                continue
            chosen = max(
                newest,
                key=lambda event: event.source_updated_at or datetime.min.replace(tzinfo=UTC),
            )
            urls: set[str] = set()
            for event in versions:
                raw_urls = event.raw_payload.get("feed_urls", [])
                if isinstance(raw_urls, list):
                    urls.update(url for url in raw_urls if isinstance(url, str))
            feed_urls: list[JsonValue] = list(sorted(urls))
            events.append(
                replace(chosen, raw_payload={**chosen.raw_payload, "feed_urls": feed_urls})
            )
        summary.eligible_records = sum(in_window(event.content, start, end) for event in events)
        logger.info("%s: %s eligible records", source.config.name, summary.eligible_records)
        observations.extend(
            Observation(source.config.slug, source.config.publisher, source.priority, event)
            for event in events
        )
    if connection is not None and source_ids is not None:
        seen = {(item.source, item.event.external_id) for item in observations}
        candidates, deferred = await omitted_events(connection, source_ids, seen, start)
        await connection.execute(
            "UPDATE eventscout.source_records SET last_recheck_at = %s WHERE id = ANY(%s)",
            (start, [item.record_id for item in candidates]),
        )
        semaphore = asyncio.Semaphore(4)
        async with _client() as client:

            async def recheck_source(source: CalendarSource, summary: SourceReport) -> None:
                slug = source.config.slug
                items = [item for item in candidates if item.source == slug]
                options = source.collect.keywords if isinstance(source.collect, partial) else {}
                with use_cache(caches[slug]):
                    for index, item in enumerate(items):
                        if index:
                            await asyncio.sleep(options.get("crawl_delay", 1))
                        summary.recheck_attempts += 1
                        async with semaphore:
                            result = await recheck_event(client, source, item.event, now=start)
                        if isinstance(result, ParseIssue):
                            summary.unconfirmed += 1
                            logger.info("%s: %s: %s", slug, result.external_id, result.message)
                        else:
                            summary.rechecked += 1
                            observations.append(
                                Observation(slug, source.config.publisher, source.priority, result)
                            )
                summary.recheck_deferred = deferred.get(slug, 0)
                if summary.unconfirmed or summary.recheck_deferred:
                    summary.coverage_complete = False
                    summary.warnings.append(
                        f"{summary.unconfirmed + summary.recheck_deferred} previously listed "
                        "upcoming events still need confirmation from their organizers."
                    )

            await asyncio.gather(
                *(
                    recheck_source(source, summary)
                    for source, summary in zip(sources, report.sources, strict=True)
                )
            )
        await save_caches(connection, source_ids, caches)
    for summary in report.sources:
        summary.requests = caches[summary.source].requests
        summary.not_modified = caches[summary.source].not_modified
    return observations


async def _catalog(connection: AsyncConnection[dict[str, Any]]) -> list[CatalogRecord]:
    cursor = await connection.execute(
        """SELECT s.slug, s.publisher, r.external_id, r.event_id,
             r.content AS observed_content, r.source_updated_at, to_jsonb(e) AS canonical_content,
             count(*) OVER (PARTITION BY r.event_id) AS record_count,
             r.raw_payload -> 'source_url_is_collection' AS source_url_is_collection
           FROM eventscout.source_records r JOIN eventscout.sources s ON s.id = r.source_id
           JOIN eventscout.event_occurrences e ON e.id = r.event_id
           WHERE e.merged_into IS NULL"""
    )
    records = []
    for row in await cursor.fetchall():
        canonical = EventContent.model_validate(
            {name: row["canonical_content"][name] for name in EventContent.model_fields}
        )
        source = SOURCES.get(row["slug"])
        records.append(
            CatalogRecord(
                source=row["slug"],
                publisher=row["publisher"],
                external_id=row["external_id"],
                priority=source.priority if source and source.enabled else 1000,
                event_id=row["event_id"],
                canonical_content=canonical,
                content=EventContent.model_validate(row["observed_content"])
                if row["observed_content"] is not None
                else canonical,
                source_updated_at=row["source_updated_at"],
                matchable=row["observed_content"] is not None or row["record_count"] == 1,
                source_url_is_collection=row["source_url_is_collection"] is True,
            )
        )
    return records


def _groups(
    observations: list[Observation],
    catalog: list[CatalogRecord],
    start: datetime,
    end: datetime,
    report: ImportReport,
) -> list[EventGroup]:
    known = {(row.source, row.external_id): row for row in catalog}
    summaries = {source.source: source for source in report.sources}
    selected = []
    for observation in observations:
        event = observation.event
        previous = known.get((observation.source, event.external_id))
        if (
            previous is not None
            and previous.source_updated_at is not None
            and event.source_updated_at is not None
            and event.source_updated_at < previous.source_updated_at
        ):
            summaries[observation.source].issues.append(
                f"{event.external_id}: older source revision ignored"
            )
        elif previous is not None or in_window(event.content, start, end):
            selected.append(observation)
    groups = group_events(selected, catalog)
    upcoming = [
        group
        for group in groups
        if any(
            member.event.content.status == "scheduled"
            and in_window(member.event.content, start, end)
            for member in group.observations
        )
    ]
    report.unique_events = len(upcoming)
    report.duplicate_source_records = sum(len(group.observations) - 1 for group in upcoming)
    for group in groups:
        if group.conflict:
            for member in group.observations:
                warning = "Ambiguous duplicate evidence; distinct occurrences were kept separate."
                if warning not in summaries[member.source].warnings:
                    summaries[member.source].warnings.append(warning)
    return groups


async def catalog_metrics(
    connection: AsyncConnection[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, int]:
    cursor = await connection.execute(
        """WITH active_records AS (
             SELECT r.event_id, r.source_id
             FROM eventscout.source_records r JOIN eventscout.sources s ON s.id = r.source_id
             WHERE s.slug = ANY(%(enabled_sources)s)
           ), live AS (
             SELECT id FROM eventscout.event_occurrences e
             WHERE merged_into IS NULL AND status = 'scheduled' AND CASE WHEN all_day THEN
               (start_date::timestamp AT TIME ZONE timezone) < %(end)s AND
               (COALESCE(end_date, start_date + 1)::timestamp AT TIME ZONE timezone) > %(start)s
             ELSE starts_at < %(end)s AND COALESCE(ends_at > %(start)s, starts_at >= %(start)s) END
             AND EXISTS (SELECT 1 FROM active_records r WHERE r.event_id = e.id)
           ) SELECT (SELECT count(*) FROM live) AS upcoming_unique_events,
             count(DISTINCT r.source_id) AS calendars,
             count(DISTINCT s.publisher) AS publishers,
             count(*) AS source_records
           FROM active_records r JOIN live ON live.id = r.event_id
           JOIN eventscout.sources s ON s.id = r.source_id""",
        {
            "start": start,
            "end": end,
            "enabled_sources": [name for name, source in SOURCES.items() if source.enabled],
        },
    )
    row = await cursor.fetchone()
    assert row is not None
    return {key: int(value) for key, value in row.items()}


async def run_import(
    settings: Settings,
    *,
    days: int = 90,
    dry_run: bool = False,
    source_names: list[str] | None = None,
) -> ImportReport:
    """Import selected calendars; repeat observations do not create canonical changes."""
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    if not source_names:
        disabled = [name for name, source in SOURCES.items() if not source.enabled]
        if disabled:
            logger.warning("Skipping disabled calendars: %s", ", ".join(disabled))
    names = source_names or [name for name, source in SOURCES.items() if source.enabled]
    sources = [SOURCES[name] for name in dict.fromkeys(names)]
    start = datetime.now(UTC)
    end = start + timedelta(days=days)
    report = ImportReport(
        dry_run=dry_run,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        sources=[
            SourceReport(source=source.config.slug, publisher=source.config.publisher)
            for source in sources
        ],
    )
    if dry_run:
        groups = _groups(await _collect(sources, start, end, report), [], start, end, report)
        report.preview = [
            group.observations[0].event.content.model_dump(mode="json") for group in groups[:5]
        ]
        return report

    async with connect_database(settings) as connection:
        cursor = await connection.execute(
            "SELECT pg_try_advisory_lock("
            "hashtextextended('eventscout:catalog-import', 0)) AS acquired"
        )
        lock = await cursor.fetchone()
        if not lock or not lock["acquired"]:
            raise RuntimeError("Another catalog import is already running")
        store = EventStore(connection)
        source_ids: dict[str, UUID] = {}
        run_ids: dict[str, UUID] = {}
        summaries = {summary.source: summary for summary in report.sources}
        catalog = await _catalog(connection)
        identities = {(row.source, row.external_id) for row in catalog}
        try:
            for source in sources:
                saved = await store.upsert_source(source.config)
                source_ids[source.config.slug] = saved.id
                run_ids[source.config.slug] = await store.start_run(saved.id)
                summaries[source.config.slug].run_id = str(run_ids[source.config.slug])
            observations = await _collect(
                sources, start, end, report, connection=connection, source_ids=source_ids
            )
            observed_at = datetime.now(UTC)
            groups = _groups(observations, catalog, start, end, report)
            pending = iter(groups)
            completed = 0

            async def worker() -> None:
                nonlocal completed
                async with connect_database(settings) as writer:
                    writer_store = EventStore(writer)
                    for group in pending:
                        primary = group.primary
                        members = sorted(
                            group.observations, key=lambda member: member is not primary
                        )
                        try:
                            results = await writer_store.upsert_group(
                                [
                                    EventObservation(
                                        source_id=source_ids[member.source],
                                        external_id=member.event.external_id,
                                        content=member.event.content,
                                        observed_at=observed_at,
                                        source_updated_at=member.event.source_updated_at,
                                        raw_payload=member.event.raw_payload,
                                        run_id=run_ids[member.source],
                                    )
                                    for member in members
                                ],
                                canonical_event_id=group.event_id,
                                merge_ids=group.merge_ids,
                                update_content=primary is not None,
                            )
                        except StaleObservationError:
                            for member in members:
                                summaries[member.source].issues.append(
                                    f"{member.event.external_id}: newer observation stored; "
                                    "group unchanged"
                                )
                            continue
                        summaries[members[0].source].merged += len(group.merge_ids)
                        for member, result in zip(members, results, strict=True):
                            summary = summaries[member.source]
                            if result.changed:
                                if result.content_version == 1:
                                    summary.created += 1
                                else:
                                    summary.updated += 1
                            elif (member.source, member.event.external_id) not in identities:
                                summary.linked += 1
                            else:
                                summary.unchanged += 1
                        completed += 1
                        if completed % 250 == 0 or completed == len(groups):
                            logger.info("Stored %s/%s unique event groups", completed, len(groups))

            async with asyncio.TaskGroup() as tasks:
                for _ in range(min(4, len(groups))):
                    tasks.create_task(worker())
        except BaseException as exc:
            for summary in report.sources:
                if summary.source not in run_ids:
                    continue
                await store.finish_run(
                    run_ids[summary.source],
                    status="failed",
                    records_seen=summary.records_seen,
                    error=f"Import interrupted ({type(exc).__name__}); rerun to resume safely.",
                )
            raise
        for summary in report.sources:
            await store.finish_run(
                run_ids[summary.source],
                status="failed" if summary.issues else "succeeded",
                records_seen=summary.records_seen,
                error="\n".join(summary.issues)[:4000] if summary.issues else None,
                coverage={
                    "window_start": report.window_start,
                    "window_end": report.window_end,
                    "requests": summary.requests,
                    "not_modified": summary.not_modified,
                    "records_seen": summary.records_seen,
                    "eligible_records": summary.eligible_records,
                    "stored": summary.created
                    + summary.updated
                    + summary.linked
                    + summary.unchanged,
                    "recheck_attempts": summary.recheck_attempts,
                    "rechecked": summary.rechecked,
                    "unconfirmed": summary.unconfirmed,
                    "recheck_deferred": summary.recheck_deferred,
                    "incomplete": not summary.coverage_complete,
                    "warnings": summary.warnings,
                },
            )
        report.catalog = await catalog_metrics(connection, start, end)
    return report
