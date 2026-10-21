"""Read public The Events Calendar REST feeds advertised by their publishers."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    MEMBERS_ONLY,
    cost_price,
    image_url,
    location_kind,
    text,
)
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection, issue_message
from app.storage.models import ATLANTA, EventContent

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


def _url(value: object, base: str) -> HttpUrl | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(base, unescape(value))
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or parts.username or parts.password:
        return None
    return HttpUrl(url)


def _utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _parse(event: dict[str, Any], base_url: str) -> ParsedEvent:
    identity = event.get("id")
    if not isinstance(identity, int) or isinstance(identity, bool) or identity <= 0:
        raise ValueError("Missing stable event ID")
    source_url = _url(event.get("url"), base_url)
    if source_url is None:
        raise ValueError("Missing event detail URL")
    title = text(event.get("title"))
    description = text(event.get("description"))
    all_day = event.get("all_day") is True
    starts_at = ends_at = None
    start_date = end_date = None
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
            and (local_start := starts_at.astimezone(ATLANTA)).weekday() == cadence[1]
            and (local_start.hour, local_start.minute) == (cadence[2], 0)
            and str(source_url).rstrip("/").endswith("/" + local_start.date().isoformat())
            and ends_at - starts_at > timedelta(days=7)
        ):
            # These weekly series give each visit the whole run's end, so leave it unknown.
            ends_at = None
    venues = event.get("venue")
    venue = venues if isinstance(venues, dict) else {}
    location = ", ".join(
        dict.fromkeys(
            value
            for field in ("venue", "address", "city", "state", "zip")
            if (value := text(venue.get(field)))
        )
    )
    website = event.get("website")
    # A ticketing or sign-up page in the website field is the registration link.
    registration = (
        _url(website, str(source_url))
        if isinstance(website, str)
        and re.search(r"eventbrite\.|seetickets\.|freshtix\.|ticket|register|rsvp", website, re.I)
        else None
    )
    price_status, price_details = cost_price(text(event.get("cost")))
    tags = [
        label
        for field in ("categories", "tags")
        for item in event.get(field, [])
        if isinstance(item, dict) and (label := text(item.get("name")))
    ]
    audience = ["Members only"] if MEMBERS_ONLY.search(title) else []
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
        venue=location or None,
        location_kind=location_kind(location),
        price_status=price_status,
        price_details=price_details,
        audience=audience,
        tags=tags,
        source_url=source_url,
        registration_url=registration,
    )
    # WordPress publishes several sizes; the 768px one suits event cards.
    image = event.get("image")
    sizes = image.get("sizes", {}) if isinstance(image, dict) else {}
    picture = image_url(sizes.get("medium_large") or image)
    return ParsedEvent(str(identity), content, revision, image_url=picture)


async def collect(
    client: httpx.AsyncClient,
    *,
    api_url: str,
    window_start: datetime,
    window_end: datetime,
    crawl_delay: float = 1,
    excluded_cities: frozenset[str] = frozenset(),
) -> SourceCollection:
    """Read up to 100 pages, skipping excluded cities and self-guided "any time" attractions."""
    result = SourceCollection()
    skipped = 0
    excluded_series: dict[str, int] = {}
    params = {
        "per_page": "50",
        "status": "publish",
        "starts_before": window_end.astimezone(ATLANTA).strftime("%Y-%m-%d %H:%M:%S"),
        "ends_after": window_start.astimezone(ATLANTA).strftime("%Y-%m-%d %H:%M:%S"),
        "strict_dates": "true",
    }
    for page in range(1, 101):
        if page > 1 and crawl_delay > 0:
            await asyncio.sleep(crawl_delay)
        try:
            response = json.loads(
                await fetch_bytes(client, api_url, params={**params, "page": str(page)})
            )
            events = response["events"]
            pages = int(response["total_pages"])
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError) as exc:
            result.issues.append(
                ParseIssue(None, f"Calendar page {page} failed: {issue_message(exc)}")
            )
            break
        result.records_seen += len(events)
        for item in events:
            if not isinstance(item, dict):
                result.issues.append(ParseIssue(None, "Expected an event object"))
                continue
            identity = str(item.get("id", ""))
            slug = str(item.get("slug", ""))
            if (
                urlsplit(api_url).hostname in {"artsatl.org", "www.artsatl.org"}
                and slug in _ARTSATL_EXCLUDED_SERIES
            ):
                excluded_series[slug] = excluded_series.get(slug, 0) + 1
                continue
            venue = item.get("venue")
            city = text(venue.get("city")).casefold() if isinstance(venue, dict) else ""
            description = text(item.get("description"))
            evergreen = re.search(
                r"\bself[ -]guided\b", str(item.get("title", "")), re.I
            ) and re.search(r"\b(?:any day|any time|at your own pace)\b", description, re.I)
            if city in excluded_cities or evergreen:
                skipped += 1
                continue
            with result.issue_if_fails(identity):
                result.events.append(_parse(item, api_url))
        if page >= pages:
            break
    else:
        result.issues.append(ParseIssue(None, "Calendar exceeded the 100-page limit"))
    if skipped:
        result.warnings.append(
            f"Excluded {skipped} out-of-area or unscheduled attraction listings."
        )
    for slug, count in sorted(excluded_series.items()):
        result.warnings.append(
            f"Excluded {count} unreliable occurrences from {slug}: "
            + _ARTSATL_EXCLUDED_SERIES[slug]
        )
    return result
