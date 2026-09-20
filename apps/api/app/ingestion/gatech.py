"""Parse Georgia Tech's RSS records without guessing missing event details."""

import asyncio
import re
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from html import unescape
from typing import Literal
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl, JsonValue, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.records import ParsedEvent, ParsedFeed, ParseIssue, SourceCollection
from app.storage.models import EventContent

_LABELS = (
    "Summary sentence",
    "Summary",
    "Event time",
    "Location",
    "Location URL",
    "Location email",
    "Location phone",
    "Fee",
    "Extras",
    "Associated importer",
    "Invited audience",
    "Mercury ID",
    "Source updated",
    "Groups",
)
_LABEL_PATTERN = re.compile(
    r"^[ \t]*(" + "|".join(re.escape(label) for label in _LABELS) + r")[ \t]*$",
    re.MULTILINE,
)

# The published RSS index incorrectly links seminars to conferences. The seminar
# listing advertises term 16 in its rel=alternate RSS link.
FEEDS = {
    "arts": 12,
    "career": 13,
    "conference": 14,
    "institute": 111,
    "other": 15,
    "seminar": 16,
    "special": 17,
    "sports": 18,
    "student": 19,
    "workshop": 20,
}


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Merge category views before filtering; retain old records for reschedule updates."""
    result = SourceCollection(events=[], records_seen=0, requests=0, issues=[])
    semaphore = asyncio.Semaphore(2)

    async def download(term: int) -> bytes | Exception:
        async with semaphore:
            try:
                return await fetch_bytes(
                    client, f"https://calendar.gatech.edu/taxonomy/term/{term}/feed"
                )
            except Exception as exc:
                return exc

    responses = await asyncio.gather(*(download(term) for term in FEEDS.values()))
    candidates: dict[str, list[tuple[ParsedEvent, str]]] = {}
    for (name, term), response in zip(FEEDS.items(), responses, strict=True):
        result.requests += 1
        url = f"https://calendar.gatech.edu/taxonomy/term/{term}/feed"
        if isinstance(response, Exception):
            if name == "student":
                result.warnings.append(
                    "Student-sponsored feed is unavailable; coverage of that category is partial"
                )
                continue
            result.issues.append(
                ParseIssue(None, f"{name}: download failed ({type(response).__name__})")
            )
            continue
        try:
            parsed = parse_feed(response)
            if parsed.records_seen == 0 and name != "special":
                raise ValueError("unexpectedly empty feed")
        except ValueError as exc:
            if name == "student":
                result.warnings.append(
                    "Student-sponsored feed is unavailable; coverage of that category is partial"
                )
                continue
            result.issues.append(ParseIssue(None, f"{name}: {exc}"))
            continue
        result.records_seen += parsed.records_seen
        result.issues.extend(parsed.issues)
        for event in parsed.events:
            candidates.setdefault(event.external_id, []).append((event, url))
    for external_id, versions in candidates.items():
        revisions = [event.source_updated_at for event, _ in versions]
        if all(revision is not None for revision in revisions):
            latest = max(revision for revision in revisions if revision is not None)
            newest = [event for event, _ in versions if event.source_updated_at == latest]
        else:
            newest = [event for event, _ in versions]
        if len({event.content.content_hash() for event in newest}) != 1:
            result.issues.append(ParseIssue(external_id, "conflicting category-feed revisions"))
            continue
        chosen = max(
            newest, key=lambda event: event.source_updated_at or datetime.min.replace(tzinfo=UTC)
        )
        if re.search(
            r"\b(?:application|grade substitution|withdrawal|progress report) deadline\b",
            chosen.content.title,
            re.I,
        ) or re.fullmatch(
            r"(?:Fall|Spring|Winter|Summer|Thanksgiving) Break"
            r"(?:\s*[-:]\s*(?:No Classes|Campus Closed))?",
            chosen.content.title,
            re.I,
        ):
            continue
        feed_urls: list[JsonValue] = list(sorted({url for _, url in versions}))
        result.events.append(
            replace(chosen, raw_payload={**chosen.raw_payload, "feed_urls": feed_urls})
        )
    return result


def _decode(value: str) -> str:
    # GT sometimes double-escapes entities in already escaped RSS HTML.
    for _ in range(3):
        decoded = unescape(value)
        if decoded == value:
            break
        value = decoded
    return value


def _text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    return " ".join(_decode(soup.get_text(" ", strip=True)).split())


def _sections(html: str) -> tuple[str, dict[str, str]]:
    matches = list(_LABEL_PATTERN.finditer(html))
    sections = {}
    for index, match in enumerate(matches):
        label = match.group(1)
        if label in sections:
            raise ValueError(f"Repeated {label!r} metadata is ambiguous")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html)
        sections[label] = html[match.end() : end].strip()
    return html[: matches[0].start()] if matches else html, sections


def _aware_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid ISO timestamp in event metadata") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Event timestamps must include a timezone offset")
    return parsed.astimezone(UTC)


def _time_values(html: str) -> list[str]:
    return [
        value
        for tag in BeautifulSoup(html, "html.parser").find_all("time")
        if isinstance(value := tag.get("datetime"), str)
    ]


def _event_time(html: str) -> tuple[datetime | None, datetime | None, date | None, date | None]:
    values = _time_values(html)
    if len(values) not in (1, 2):
        raise ValueError("Expected one or two timestamps in Event time")
    date_only = [bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)) for value in values]
    if all(date_only):
        dates = [date.fromisoformat(value) for value in values]
        if len(dates) == 2 and dates[1] != dates[0]:
            raise ValueError(
                "Multi-day date-only event needs explicit inclusive/exclusive end semantics"
            )
        return None, None, dates[0], dates[0] + timedelta(days=1)
    if any(date_only):
        raise ValueError("Event time mixes dates and timestamps")
    times = [_aware_time(value) for value in values]
    if re.search(r"\ball[ -]day\b", _text(html), re.IGNORECASE):
        raise ValueError("All-day event has timestamps; date boundaries need review")
    if len(times) == 2 and times[1] <= times[0]:
        raise ValueError("Event end must be after its start")
    return times[0], times[1] if len(times) == 2 else None, None, None


def _safe_url(value: str, *, base: str = "") -> HttpUrl:
    decoded = _decode(value).strip()
    url = urljoin(base, decoded) if base else decoded
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or parts.username or parts.password:
        raise ValueError("Event links must be HTTP(S) URLs without embedded credentials")
    return HttpUrl(url)


def _description(prefix: str, sections: dict[str, str]) -> str:
    soup = BeautifulSoup(prefix, "html.parser")
    # Drupal prepends the title, author, and publication timestamp in top-level spans.
    removed = 0
    for node in list(soup.children):
        if isinstance(node, Tag) and node.name == "span" and removed < 3:
            node.decompose()
            removed += 1
        elif str(node).strip():
            break
    body = _text(str(soup))
    return body or _text(sections.get("Summary", sections.get("Summary sentence", "")))


def _leading_text(html: str) -> str:
    # The bare field value precedes contact paragraphs and unrelated nested metadata.
    return _text(html.split("<", maxsplit=1)[0])


def _labels(soup: BeautifulSoup, path: str) -> list[str]:
    return sorted(
        {
            _text(str(tag))
            for tag in soup.find_all("a")
            if isinstance(href := tag.get("href"), str)
            and urlsplit(href).hostname == "calendar.gatech.edu"
            and urlsplit(href).path.startswith(path)
            and _text(str(tag))
        }
    )


def _registration_url(soup: BeautifulSoup, source_url: str, description: str) -> HttpUrl | None:
    candidates: list[tuple[int, str, HttpUrl]] = []
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        label = _text(str(anchor))
        explicit_label = bool(
            re.search(
                r"\b(register|registration|rsvp|sign[ -]?up|tickets?|join online)\b", label, re.I
            )
        )
        registration_path = bool(
            re.search(r"[/_-](register|registration|rsvp)(?:[/_?=-]|$)", href, re.I)
        )
        context = anchor.find_parent(["p", "li"])
        generic_label = label.casefold() in {"here", "click here"} or label.startswith(
            ("https://", "http://", "www.")
        )
        contextual_link = (
            generic_label
            and context is not None
            and bool(
                re.search(
                    r"\b(register|registration|rsvp|tickets?|sign[ -]?up)\b",
                    context.get_text(" "),
                    re.I,
                )
            )
        )
        previous = context.find_previous_sibling("p") if context is not None else None
        preceding_instruction = (
            generic_label
            and previous is not None
            and bool(re.search(r"\bregister\b", previous.get_text(" "), re.I))
            and bool(re.search(r"\blink below\b", previous.get_text(" "), re.I))
        )
        if not (explicit_label or registration_path or contextual_link or preceding_instruction):
            continue
        try:
            url = _safe_url(href, base=source_url)
        except ValueError:
            continue
        score = 2 * int(explicit_label or contextual_link) + int(registration_path)
        candidates.append((score, str(url), url))
    # Some organizers put an explicit registration URL in plain text instead of an anchor.
    for match in re.finditer(
        r"\b(?:register|registration|rsvp|sign[ -]?up)\b[^.!?\n]{0,60}(https?://\S+)",
        description,
        re.IGNORECASE,
    ):
        try:
            url = _safe_url(match.group(1).rstrip(".,;)"))
        except ValueError:
            continue
        candidates.append((1, str(url), url))
    return (
        max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]
        if candidates
        else None
    )


def _price(
    fee: str, tags: list[str], description: str
) -> tuple[Literal["free", "paid", "conditional", "unknown"], str | None]:
    value = fee.casefold().strip(" .")
    if value in {"free", "no cost", "no fee", "there is no fee", "$0", "$0.00", "0", "0.00"}:
        return "free", fee
    if value and value not in {"n/a", "na", "unknown", "tbd"}:
        if re.search(r"\bfree\b|\bvaries\b", value):
            return "conditional", fee
        if re.search(r"\$\s*\d|^\d+(?:\.\d{2})?$", value):
            return "paid", fee
        # Instructions to visit a registration page do not establish a price.
        return "unknown", fee
    if "free" in {tag.casefold() for tag in tags}:
        return "free", "Listed as Free by Georgia Tech"
    if match := re.search(
        r"\b(?:free (?:admission|entry|registration|workshop|event|webinar|seminar)|"
        r"(?:admission|entry|registration|event|workshop|session|webinar|conference|seminar)"
        r" (?:is|will be) free)"
        r"(?:\s+for\s+[^.!?]+)?",
        description,
        re.IGNORECASE,
    ):
        evidence = match.group(0)
        return ("conditional" if re.search(r"\bfor\b", evidence, re.I) else "free"), evidence
    return "unknown", None


def _location_kind(venue: str | None) -> Literal["in_person", "online", "hybrid", "unknown"]:
    if not venue or venue.casefold() in {"n/a", "tbd", "tba", "related link", "see description"}:
        return "unknown"
    if re.search(r"\bhybrid\b", venue, re.I):
        return "hybrid"
    if re.match(r"(?:online|virtual|zoom|webinar|microsoft teams)\b", venue, re.I):
        return "online"
    if re.search(r"\b(online|virtual|zoom)\b", venue, re.I):
        return "unknown"
    return "in_person"


def _parse_item(item: ElementTree.Element, external_id: str) -> ParsedEvent:
    title = _text(item.findtext("title", ""))
    source_url = _safe_url(item.findtext("link", ""))
    html = item.findtext("description", "")
    prefix, sections = _sections(html)
    starts_at, ends_at, start_date, end_date = _event_time(sections.get("Event time", ""))
    description = _description(prefix, sections)
    soup = BeautifulSoup(html, "html.parser")
    tags = _labels(soup, "/event/listings/")
    audience = _labels(soup, "/event/invited-audience/")
    venue = _leading_text(sections.get("Location", "")) or None
    price_status, price_details = _price(_leading_text(sections.get("Fee", "")), tags, description)
    updated_values = _time_values(sections.get("Source updated", ""))
    if len(updated_values) > 1:
        raise ValueError("Multiple Source updated timestamps are ambiguous")
    source_updated_at = _aware_time(updated_values[0]) if updated_values else None
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=start_date is not None,
        start_date=start_date,
        end_date=end_date,
        venue=venue,
        location_kind=_location_kind(venue),
        region="gt",
        price_status=price_status,
        price_details=price_details,
        audience=audience,
        tags=tags,
        source_url=source_url,
        registration_url=_registration_url(soup, str(source_url), description),
        status="cancelled"
        if re.match(r"^(?:\[|\()?(?:cancelled|canceled)\b", title, re.I)
        else "scheduled",
    )
    return ParsedEvent(
        external_id=external_id,
        content=content,
        source_updated_at=source_updated_at,
        raw_payload={
            "guid": external_id,
            "title": item.findtext("title", ""),
            "link": item.findtext("link", ""),
            "description_html": html,
            "pub_date": item.findtext("pubDate"),
            "mercury_id": _leading_text(sections.get("Mercury ID", "")) or None,
        },
    )


def parse_feed(xml: bytes) -> ParsedFeed:
    """Return all valid records so callers can merge revisions before filtering dates.

    Invalid records become issues; invalid XML/feed structure fails the entire feed
    with ValueError. No publication timestamp is used as an event's start time.
    """
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.I) or b"\x00" in xml:
        raise ValueError("RSS DTD/entity declarations and null bytes are not supported")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("Georgia Tech returned invalid RSS XML") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected a Georgia Tech RSS channel")
    items = root.findall("./channel/item")
    result = ParsedFeed(events=[], records_seen=len(items), issues=[])
    identities = Counter((item.findtext("guid") or "").strip() for item in items)
    for item in items:
        external_id = (item.findtext("guid") or "").strip()
        if not external_id:
            result.issues.append(ParseIssue(None, "Missing stable RSS GUID"))
            continue
        if identities[external_id] > 1:
            result.issues.append(ParseIssue(external_id, "Repeated RSS GUID in the same feed"))
            continue
        try:
            parsed = _parse_item(item, external_id)
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(str(part) for part in error["loc"]) or "URL"
                for error in exc.errors(include_input=False)
            )
            result.issues.append(ParseIssue(external_id, f"Invalid event fields: {fields}"))
            continue
        except ValueError as exc:
            result.issues.append(ParseIssue(external_id, str(exc)))
            continue
        result.events.append(parsed)
    return result
