"""Reconcile exact occurrence evidence while preserving existing catalog identities."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from app.ingestion.records import ParsedEvent
from app.storage.models import EventContent


@dataclass(frozen=True)
class Observation:
    source: str
    publisher: str
    priority: int
    event: ParsedEvent


@dataclass(frozen=True)
class CatalogRecord:
    source: str
    publisher: str
    priority: int
    external_id: str
    event_id: UUID
    content: EventContent
    source_url_is_collection: bool = False


@dataclass
class EventGroup:
    observations: list[Observation]
    event_id: UUID | None
    authority: str
    conflict: bool = False


def _label(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def _url(value: str) -> str:
    parts = urlsplit(value)
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in {"fbclid", "gclid"}
    )
    return urlunsplit(
        ("https", parts.netloc.casefold(), parts.path.rstrip("/"), urlencode(query), "")
    )


def _keys(
    publisher: str, content: EventContent, source_url_is_collection: bool = False
) -> set[tuple[str, ...]]:
    when = str(content.start_date if content.all_day else content.starts_at)
    title = _label(content.title)
    keys: set[tuple[str, ...]] = set()
    if not source_url_is_collection:
        keys.add(("source-url", _url(str(content.source_url)), when))
    if content.registration_url is not None:
        keys.add(("registration", _url(str(content.registration_url)), title, when))
    if content.venue and content.location_kind != "unknown":
        keys.add(("publisher-title-venue", publisher, title, when, _label(content.venue)))
    return keys


def group_events(observations: list[Observation], catalog: list[CatalogRecord]) -> list[EventGroup]:
    """Match exact links or same-publisher title/time/venue; never fuzzy-merge titles."""
    existing_identity = {(row.source, row.external_id): row.event_id for row in catalog}
    existing_keys: dict[tuple[str, ...], set[UUID]] = {}
    authorities: dict[UUID, tuple[int, str]] = {}
    for row in catalog:
        for key in _keys(row.publisher, row.content, row.source_url_is_collection):
            existing_keys.setdefault(key, set()).add(row.event_id)
        authorities[row.event_id] = min(
            authorities.get(row.event_id, (row.priority, row.source)), (row.priority, row.source)
        )
    parents = list(range(len(observations)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen: dict[tuple[str, ...], int] = {}
    ids: list[set[UUID]] = []
    for index, observation in enumerate(observations):
        keys = _keys(
            observation.publisher,
            observation.event.content,
            observation.event.raw_payload.get("source_url_is_collection") is True,
        )
        known_ids: set[UUID] = set()
        identity = (observation.source, observation.event.external_id)
        if identity in existing_identity:
            known_ids.add(existing_identity[identity])
        for key in keys:
            known_ids.update(existing_keys.get(key, set()))
        ids.append(known_ids)
        keys.update(("canonical-id", str(event_id)) for event_id in known_ids)
        for key in keys:
            if key in seen:
                parents[root(index)] = root(seen[key])
            else:
                seen[key] = index
    groups: dict[int, list[int]] = {}
    for index in range(len(observations)):
        groups.setdefault(root(index), []).append(index)
    results: list[EventGroup] = []
    for indices in groups.values():
        members = sorted(
            (observations[index] for index in indices),
            key=lambda item: (item.priority, item.source, item.event.external_id),
        )
        known = {event_id for index in indices for event_id in ids[index]}
        authority = min(
            [(member.priority, member.source) for member in members]
            + [authorities[event_id] for event_id in known]
        )[1]
        results.append(
            EventGroup(
                observations=members,
                event_id=next(iter(known)) if len(known) == 1 else None,
                authority=authority,
                conflict=len(known) > 1,
            )
        )
    return results
