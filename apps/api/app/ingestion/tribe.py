"""Read public The Events Calendar REST feeds advertised by their publishers."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from bs4 import BeautifulSoup
from pydantic import HttpUrl, ValidationError

from app.ingestion.http import fetch_bytes
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection, in_window
from app.storage.models import EventContent

_LOCAL_ZONE = ZoneInfo("America/New_York")
_ARTSATL_WEEKLY_SERIES = {
    "uptown-market-atlanta-2": (r"\bevery Saturday\b", 5, 11),
    "live-music-in-the-beer-garden-oktoberfest-music-series": (r"\bevery Friday\b", 4, 19),
}
_ARTSATL_EXCLUDED_SERIES = {
    "rudolph-the-red-nosed-reindeer-3": (
        "Repeated start times conflict with the published weekday performance schedule."
    ),
    "everybody-loves-pirates": (
        "Repeated start times conflict with the Center for Puppetry Arts performance calendar."
    ),
    "attend-inner-views-art-exhibition": (
        "Generated future dates conflict with the exhibition's published January-March run."
    ),
}


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup.select("script, style"):
        tag.decompose()
    return " ".join(unescape(soup.get_text(" ", strip=True)).split())


def _url(value: object, base: str) -> HttpUrl | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(base, unescape(value))
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or parts.username or parts.password:
        return None
    return HttpUrl(url)


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Missing published UTC event timestamp")
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _price(cost: str) -> tuple[Literal["free", "paid", "conditional", "unknown"], str | None]:
    if not cost:
        return "unknown", None
    if cost.casefold() in {"free", "0", "$0", "0.00", "$0.00", "no charge"}:
        return "free", cost
    if re.search(r"\b(?:free|donation|members?|varies)\b", cost, re.I):
        return "conditional", cost
    if re.search(r"\d", cost):
        return "paid", cost
    return "unknown", cost


def _parse(event: dict[str, Any], base_url: str) -> ParsedEvent:
    identity = event.get("id")
    if not isinstance(identity, int) or isinstance(identity, bool) or identity <= 0:
        raise ValueError("Missing stable event ID")
    source_url = _url(event.get("url"), base_url)
    if source_url is None:
        raise ValueError("Missing event detail URL")
    title = _text(event.get("title"))
    description = _text(event.get("description"))
    all_day = event.get("all_day") is True
    zone = event.get("timezone", "America/New_York")
    try:
        if not isinstance(zone, str):
            raise ValueError("Missing timezone")
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        # UTC timestamps remain authoritative if a publisher uses an offset label.
        zone = "America/New_York"
    starts_at = ends_at = None
    start_date = end_date = None
    ignored_end_reason = None
    if all_day:
        start_date = datetime.fromisoformat(str(event.get("start_date"))).date()
        # Tribe publishes all-day end dates inclusively, at 23:59:59.
        end_date = datetime.fromisoformat(str(event.get("end_date"))).date() + timedelta(days=1)
    else:
        starts_at = _utc(event.get("utc_start_date"))
        ends_at = _utc(event.get("utc_end_date"))
        if ends_at == starts_at:
            ends_at = None
        elif (
            urlsplit(base_url).hostname in {"artsatl.org", "www.artsatl.org"}
            and source_url.host in {"artsatl.org", "www.artsatl.org"}
            and (cadence := _ARTSATL_WEEKLY_SERIES.get(str(event.get("slug"))))
            and re.search(cadence[0], description, re.I)
            and (local_start := starts_at.astimezone(ZoneInfo(zone))).weekday() == cadence[1]
            and (local_start.hour, local_start.minute) == (cadence[2], 0)
            and str(source_url)
            .rstrip("/")
            .endswith("/" + starts_at.astimezone(ZoneInfo(zone)).date().isoformat())
            and ends_at - starts_at > timedelta(days=7)
        ):
            # These verified weekly series inherit their whole run's duration.
            # Dated URLs and an explicit weekly cadence establish separate
            # visits, but conflicting published clocks do not justify an end.
            ignored_end_reason = (
                "Weekly occurrence has a conflicting multi-week published end; end time is unknown."
            )
            ends_at = None
    venues = event.get("venue")
    venue = venues if isinstance(venues, dict) else {}
    location = ", ".join(
        dict.fromkeys(
            value
            for field in ("venue", "address", "city", "state", "zip")
            if (value := _text(venue.get(field)))
        )
    )
    location_kind: Literal["in_person", "online", "hybrid", "unknown"] = "unknown"
    if re.search(r"\bhybrid\b", location, re.I):
        location_kind = "hybrid"
    elif re.search(r"\b(?:online|virtual|zoom)\b", location, re.I):
        location_kind = "online"
    elif location and not re.search(r"\b(?:tba|tbd|various locations)\b", location, re.I):
        location_kind = "in_person"
    registration_url = None
    for anchor in BeautifulSoup(str(event.get("description", "")), "html.parser").select("a"):
        if re.search(
            r"\b(?:register|registration|rsvp|tickets?|sign[ -]?up)\b", anchor.get_text(), re.I
        ):
            registration_url = _url(anchor.get("href"), str(source_url))
            if registration_url is not None:
                break
    website = event.get("website")
    if (
        registration_url is None
        and isinstance(website, str)
        and re.search(
            r"(?:eventbrite\.|seetickets\.|freshtix\.|ticket|register|registration|rsvp)",
            website,
            re.I,
        )
    ):
        registration_url = _url(website, str(source_url))
    cost = _text(event.get("cost"))
    price_status, price_details = _price(cost)
    tags = [
        label
        for field in ("categories", "tags")
        for item in event.get(field, [])
        if isinstance(item, dict) and (label := _text(item.get("name")))
    ]
    audience = ["Members only"] if re.search(r"\bmembers?[ -]only\b", title, re.I) else []
    modified = event.get("modified_utc")
    revision = (
        _utc(modified) if isinstance(modified, str) and not modified.startswith("0000-") else None
    )
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        start_date=start_date,
        end_date=end_date,
        timezone=zone,
        venue=location or None,
        location_kind=location_kind,
        region="atlanta",
        price_status=price_status,
        price_details=price_details,
        audience=audience,
        tags=tags,
        source_url=source_url,
        registration_url=registration_url,
        status="cancelled"
        if re.match(r"^(?:\[|\()?(?:cancelled|canceled)\b", title, re.I)
        else "scheduled",
    )
    raw_payload = {**event, "feed_urls": [base_url]}
    if ignored_end_reason is not None:
        raw_payload["ignored_end_reason"] = ignored_end_reason
    return ParsedEvent(str(identity), content, revision, raw_payload)


async def collect(
    client: httpx.AsyncClient,
    *,
    api_url: str,
    window_start: datetime,
    window_end: datetime,
    crawl_delay: float = 1,
    excluded_cities: frozenset[str] = frozenset(),
    overlap_filters: bool = True,
) -> SourceCollection:
    """Read up to 100 pages, preserving published recurrence IDs and filtering locally.

    The API's overlapping-date filters include ongoing exhibitions. Perpetual
    self-guided attractions and events in explicitly excluded cities are omitted.
    """
    result = SourceCollection(events=[], records_seen=0, requests=0, issues=[])
    seen: set[str] = set()
    skipped = 0
    corrected_ends = 0
    excluded_series: dict[str, int] = {}
    params = {
        "per_page": "50",
        "status": "publish",
    }
    if overlap_filters:
        params.update(
            starts_before=window_end.astimezone(_LOCAL_ZONE).strftime("%Y-%m-%d %H:%M:%S"),
            ends_after=window_start.astimezone(_LOCAL_ZONE).strftime("%Y-%m-%d %H:%M:%S"),
            strict_dates="true",
        )
    else:
        params.update(
            start_date=window_start.astimezone(_LOCAL_ZONE).strftime("%Y-%m-%d"),
            end_date=window_end.astimezone(_LOCAL_ZONE).strftime("%Y-%m-%d"),
        )
    for page in range(1, 101):
        if page > 1 and crawl_delay > 0:
            await asyncio.sleep(crawl_delay)
        result.requests += 1
        try:
            response = json.loads(
                await fetch_bytes(client, api_url, params={**params, "page": str(page)})
            )
            if not isinstance(response, dict) or not isinstance(response.get("events"), list):
                raise ValueError("Expected a public Tribe events collection")
            events = response["events"]
            pages = response.get("total_pages")
            if not isinstance(pages, int) or isinstance(pages, bool) or pages < 0:
                raise ValueError("Missing valid pagination total")
            if not events and pages > 0:
                raise ValueError("Calendar returned an unexpectedly empty page")
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            message = (
                f"HTTP {exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else str(exc)
                if isinstance(exc, ValueError)
                else type(exc).__name__
            )
            result.issues.append(ParseIssue(None, f"Calendar page {page} failed: {message}"))
            break
        result.records_seen += len(events)
        for item in events:
            if not isinstance(item, dict):
                result.issues.append(ParseIssue(None, "Expected an event object"))
                continue
            identity = str(item.get("id", ""))
            if identity in seen:
                result.issues.append(
                    ParseIssue(identity, "Repeated event ID across calendar pages")
                )
                continue
            seen.add(identity)
            slug = str(item.get("slug", ""))
            if (
                urlsplit(api_url).hostname in {"artsatl.org", "www.artsatl.org"}
                and slug in _ARTSATL_EXCLUDED_SERIES
            ):
                excluded_series[slug] = excluded_series.get(slug, 0) + 1
                continue
            venue = item.get("venue")
            city = _text(venue.get("city")).casefold() if isinstance(venue, dict) else ""
            text = _text(item.get("description"))
            evergreen = re.search(
                r"\bself[ -]guided\b", str(item.get("title", "")), re.I
            ) and re.search(r"\b(?:any day|any time|at your own pace)\b", text, re.I)
            if city in excluded_cities or evergreen:
                skipped += 1
                continue
            try:
                event = _parse(item, api_url)
                corrected_end = "ignored_end_reason" in event.raw_payload
                corrected_ends += corrected_end
                # Return corrections even after their true occurrence is past;
                # the runner refreshes known rows without inserting past ones.
                if corrected_end or in_window(event.content, window_start, window_end):
                    result.events.append(event)
            except (ValueError, TypeError) as exc:
                message = "Invalid event fields" if isinstance(exc, ValidationError) else str(exc)
                result.issues.append(ParseIssue(identity, message))
        if page >= pages:
            break
    else:
        result.issues.append(ParseIssue(None, "Calendar exceeded the 100-page limit"))
    if skipped:
        result.warnings.append(
            f"Excluded {skipped} out-of-area or unscheduled attraction listings."
        )
    if corrected_ends:
        result.warnings.append(
            f"Kept {corrected_ends} weekly occurrence end times unknown because the "
            "publisher supplied conflicting multi-week ends; originals remain in metadata."
        )
    for slug, count in sorted(excluded_series.items()):
        result.warnings.append(
            f"Excluded {count} unreliable occurrences from {slug}: "
            + _ARTSATL_EXCLUDED_SERIES[slug]
        )
    result.events.sort(key=lambda event: event.external_id)
    return result
