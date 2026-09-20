"""Match occurrence evidence without collapsing recurring sessions or reschedules."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
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
    canonical_content: EventContent | None = None
    source_updated_at: datetime | None = None
    matchable: bool = True


@dataclass
class EventGroup:
    observations: list[Observation]
    event_id: UUID | None
    authority: str
    primary: Observation | None = None
    merge_ids: tuple[UUID, ...] = ()
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


def _when(content: EventContent) -> str:
    if content.all_day:
        return f"{content.timezone}:{content.start_date}"
    return str(content.starts_at) if content.starts_at else ""


def _revision(item: Observation) -> tuple[datetime, str]:
    return (
        item.event.source_updated_at or datetime.min.replace(tzinfo=UTC),
        item.event.external_id,
    )


def _keys(
    publisher: str, content: EventContent, source_url_is_collection: bool = False
) -> set[tuple[str, ...]]:
    when = _when(content)
    if not when:
        return set()
    title = _label(content.title)
    keys: set[tuple[str, ...]] = set()
    if not source_url_is_collection:
        url = _url(str(content.source_url))
        keys.update({("source-url", url, when), ("registration", url, title, when)})
    if content.registration_url is not None:
        keys.add(("registration", _url(str(content.registration_url)), title, when))
    if content.venue and content.location_kind == "in_person":
        keys.add(("publisher-title-venue", publisher, title, when, _label(content.venue)))
        descriptions = {_label(content.description)}
        heading, separator, body = content.description.partition("\n\n")
        # Some publishers put a standalone room heading before the same event text.
        if separator and len(heading) < 80 and not re.search(r"[.!?]", heading):
            descriptions.add(_label(body))
        venue = _label(content.venue.split(",", 1)[0])
        for description in descriptions:
            if len(description) >= 200:
                keys.add(("description-title-venue", title, when, venue, description))
    return keys


def group_events(observations: list[Observation], catalog: list[CatalogRecord]) -> list[EventGroup]:
    """Replace old evidence with current observations, then group conservative exact keys."""
    existing = {(row.source, row.external_id): row for row in catalog}
    current = {(item.source, item.event.external_id): item for item in observations}
    authorities: dict[UUID, tuple[int, str]] = {}
    canonical_times: dict[UUID, str] = {}
    for row in catalog:
        authorities[row.event_id] = min(
            authorities.get(row.event_id, (row.priority, row.source)), (row.priority, row.source)
        )
        canonical_times[row.event_id] = _when(row.canonical_content or row.content)

    moved: set[UUID] = set()
    nodes: list[Observation] = []
    known: list[UUID | None] = []
    matchable: list[bool] = []
    for identity in sorted(existing.keys() | current.keys()):
        previous = existing.get(identity)
        item = current.get(identity)
        if item is None:
            assert previous is not None
            item = Observation(
                previous.source,
                previous.publisher,
                previous.priority,
                ParsedEvent(
                    previous.external_id,
                    previous.content,
                    previous.source_updated_at,
                    {"source_url_is_collection": previous.source_url_is_collection},
                ),
            )
        elif previous is not None and _when(item.event.content) != _when(previous.content):
            moved.add(previous.event_id)
        nodes.append(item)
        known.append(previous.event_id if previous else None)
        matchable.append(identity in current or (previous is not None and previous.matchable))

    preferred: dict[UUID, Observation] = {}
    for item, event_id in zip(nodes, known, strict=True):
        if event_id is not None and item.source == authorities[event_id][1]:
            if event_id not in preferred or _revision(item) > _revision(preferred[event_id]):
                preferred[event_id] = item
    for event_id, item in preferred.items():
        if (item.source, item.event.external_id) in current:
            canonical_times[event_id] = _when(item.event.content)

    parents = list(range(len(nodes)))
    members = {index: {index} for index in parents}

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left: int, right: int) -> None:
        left, right = root(left), root(right)
        if left == right:
            return
        parents[right] = left
        members[left].update(members.pop(right))

    by_id: dict[UUID, int] = {}
    for index, event_id in enumerate(known):
        if event_id is not None:
            if event_id in by_id:
                join(index, by_id[event_id])
            by_id[event_id] = index

    seen: dict[tuple[str, ...], int] = {}
    for index, item in enumerate(nodes):
        event_id = known[index]
        # Stale secondary dates remain provenance, not evidence for another occurrence.
        if not matchable[index] or (
            event_id is not None and _when(item.event.content) != canonical_times[event_id]
        ):
            continue
        for key in _keys(
            item.publisher,
            item.event.content,
            item.event.raw_payload.get("source_url_is_collection") is True,
        ):
            if key in seen:
                join(index, seen[key])
            seen[key] = index

    components: list[tuple[set[int], bool]] = []
    for indices in members.values():
        event_ids = {value for i in indices if (value := known[i]) is not None}
        # Judge the whole component, so an ambiguous bridge cannot win by input order.
        conflict = len(event_ids) > 1 and bool(event_ids & moved)
        sessions: dict[tuple[str, str], set[UUID | int]] = {}
        for i in indices:
            item = nodes[i]
            key = (item.source, _url(str(item.event.content.source_url)))
            sessions.setdefault(key, set()).add(known[i] or i)
        conflict |= any(len(ids) > 1 for ids in sessions.values())
        if conflict:
            established: dict[UUID | int, set[int]] = {}
            for i in indices:
                established.setdefault(known[i] or i, set()).add(i)
            components.extend((group, True) for group in established.values())
        else:
            components.append((indices, False))

    results = []
    for indices, conflict in components:
        incoming = sorted(
            (nodes[i] for i in indices if (nodes[i].source, nodes[i].event.external_id) in current),
            key=lambda item: (item.priority, item.source, item.event.external_id),
        )
        if not incoming:
            continue
        event_ids = {value for i in indices if (value := known[i]) is not None}
        winner = (
            min(event_ids, key=lambda event_id: (authorities[event_id], str(event_id)))
            if event_ids
            else None
        )
        authority = min(
            [(item.priority, item.source) for item in incoming]
            + [authorities[event_id] for event_id in event_ids]
        )[1]
        best = max((nodes[i] for i in indices if nodes[i].source == authority), key=_revision)
        results.append(
            EventGroup(
                observations=incoming,
                event_id=winner,
                authority=authority,
                primary=best if (best.source, best.event.external_id) in current else None,
                merge_ids=tuple(sorted(event_ids - {winner}, key=str)),
                conflict=conflict,
            )
        )
    return results
