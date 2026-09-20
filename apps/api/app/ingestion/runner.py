"""Collect public calendars, reconcile exact matches, and persist bounded imports."""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from psycopg import AsyncConnection
from pydantic import JsonValue

from app.database import connect_database
from app.ingestion.matching import CatalogRecord, EventGroup, Observation, group_events
from app.ingestion.records import ParsedEvent, in_window
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
    records_seen: int = 0
    eligible_records: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    linked: int = 0
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


async def _collect(
    sources: list[CalendarSource], start: datetime, end: datetime, report: ImportReport
) -> list[Observation]:
    async with httpx.AsyncClient(
        headers={"User-Agent": "EventScout/0.1 (public calendar event discovery)"},
        timeout=httpx.Timeout(25),
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(retries=2, limits=httpx.Limits(max_connections=8)),
    ) as client:
        collections = await asyncio.gather(
            *(source.collect(client, window_start=start, window_end=end) for source in sources),
            return_exceptions=True,
        )
    observations: list[Observation] = []
    for source, collection, summary in zip(sources, collections, report.sources, strict=True):
        if isinstance(collection, BaseException):
            summary.issues.append(f"Collection failed ({type(collection).__name__})")
            continue
        summary.requests = collection.requests
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
    return observations


async def _catalog(connection: AsyncConnection[dict[str, Any]]) -> list[CatalogRecord]:
    cursor = await connection.execute(
        """SELECT s.slug, s.publisher, r.external_id, r.event_id, to_jsonb(e) AS content,
             r.raw_payload -> 'source_url_is_collection' AS source_url_is_collection
           FROM eventscout.source_records r JOIN eventscout.sources s ON s.id = r.source_id
           JOIN eventscout.event_occurrences e ON e.id = r.event_id"""
    )
    return [
        CatalogRecord(
            source=row["slug"],
            publisher=row["publisher"],
            external_id=row["external_id"],
            priority=SOURCES[row["slug"]].priority if row["slug"] in SOURCES else 100,
            event_id=row["event_id"],
            content=EventContent.model_validate(
                {name: row["content"][name] for name in EventContent.model_fields}
            ),
            source_url_is_collection=row["source_url_is_collection"] is True,
        )
        for row in await cursor.fetchall()
    ]


def _groups(
    observations: list[Observation],
    catalog: list[CatalogRecord],
    start: datetime,
    end: datetime,
    report: ImportReport,
) -> list[EventGroup]:
    known = {(row.source, row.external_id) for row in catalog}
    selected = [
        observation
        for observation in observations
        if in_window(observation.event.content, start, end)
        or (observation.source, observation.event.external_id) in known
    ]
    groups = group_events(selected, catalog)
    accepted = [group for group in groups if not group.conflict]
    upcoming = [
        group
        for group in accepted
        if any(
            member.event.content.status == "scheduled"
            and in_window(member.event.content, start, end)
            for member in group.observations
        )
    ]
    report.unique_events = len(upcoming)
    report.duplicate_source_records = sum(len(group.observations) - 1 for group in upcoming)
    summaries = {source.source: source for source in report.sources}
    for group in groups:
        if group.conflict:
            for member in group.observations:
                summaries[member.source].issues.append(
                    f"{member.event.external_id}: matches multiple existing events; review required"
                )
    return accepted


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
             WHERE status = 'scheduled' AND CASE WHEN all_day THEN
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
            observed_at = datetime.now(UTC)
            groups = _groups(
                await _collect(sources, start, end, report), catalog, start, end, report
            )
            pending = iter(groups)
            completed = 0

            async def worker() -> None:
                nonlocal completed
                async with connect_database(settings) as writer:
                    writer_store = EventStore(writer)
                    for group in pending:
                        primary = max(
                            (
                                member
                                for member in group.observations
                                if member.source == group.authority
                            ),
                            key=lambda member: (
                                member.event.source_updated_at or datetime.min.replace(tzinfo=UTC),
                                member.event.external_id,
                            ),
                            default=None,
                        )
                        members = sorted(
                            group.observations, key=lambda member: member is not primary
                        )
                        event_id = group.event_id
                        for member in members:
                            summary = summaries[member.source]
                            event = member.event
                            try:
                                result = await writer_store.upsert_event(
                                    EventObservation(
                                        source_id=source_ids[member.source],
                                        external_id=event.external_id,
                                        content=event.content,
                                        observed_at=observed_at,
                                        source_updated_at=event.source_updated_at,
                                        raw_payload=event.raw_payload,
                                        run_id=run_ids[member.source],
                                    ),
                                    canonical_event_id=event_id,
                                    update_content=member is primary,
                                )
                            except StaleObservationError:
                                summary.issues.append(
                                    f"{event.external_id}: newer observation already stored"
                                )
                                continue
                            event_id = result.event_id
                            if result.changed:
                                if result.content_version == 1:
                                    summary.created += 1
                                else:
                                    summary.updated += 1
                            elif (member.source, event.external_id) not in identities:
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
            )
        report.catalog = await catalog_metrics(connection, start, end)
    return report
