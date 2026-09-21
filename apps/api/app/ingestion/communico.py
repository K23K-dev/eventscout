"""Merge public branch subscriptions into one calendar per library system."""

import asyncio
import base64
import json
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from icalendar import Event
from pydantic import JsonValue

from app.ingestion.http import fetch_bytes
from app.ingestion.icalendar_feed import _parse_event, parse_feed
from app.ingestion.parsing import html_text, localize
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection

CALENDARS = {
    "dekalb": "https://events.dekalblibrary.org",
    "gwinnettpl": "https://gwinnettpl.libnet.info",
}
_FEED_LIMIT = 500


def parse_detail(data: bytes, url: str) -> ParsedEvent:
    """Use the detail's own Event schema when an occurrence leaves a capped feed."""
    soup = BeautifulSoup(data, "html.parser")
    schemas = [
        item
        for script in soup.select('script[type="application/ld+json"]')
        if isinstance(item := json.loads(script.get_text()), dict)
        and item.get("@type") == "Event"
        and str(item.get("url", "")).rstrip("/") == url.rstrip("/")
    ]
    if len(schemas) != 1:
        raise ValueError("Missing unambiguous library event details")
    schema = schemas[0]
    identity = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if not identity.isdecimal():
        raise ValueError("Missing stable library event ID")
    event = Event()
    event.add("uid", identity)
    event.add("summary", html_text(schema["name"], decode_entities=True))
    event.add("description", html_text(schema.get("description", ""), decode_entities=True))
    for field, property_name in (("startDate", "dtstart"), ("endDate", "dtend")):
        value = datetime.fromisoformat(schema[field])
        if value.tzinfo is None:
            value = localize(value, ZoneInfo("America/New_York"))
        event.add(property_name, value)
    location = schema.get("location", {})
    event.add("location", html_text(location.get("name", ""), decode_entities=True))
    event.add("url", url)
    event.add(
        "status",
        "CANCELLED"
        if str(schema.get("eventStatus", "")).endswith("/EventCancelled")
        else "CONFIRMED",
    )
    return _parse_event(event, fallback_url=url, event_base_url=None, midnight_all_day=True)


def _feed_params(location_id: str) -> dict[str, str]:
    # Same payload as the publisher's Add to Calendar control. Its UI explicitly
    # documents a 500-event cap and that the selected date range is not exported.
    filters = {
        "feedType": "ical",
        "filters": {
            "location": [location_id],
            "ages": ["all"],
            "types": ["all"],
            "tags": [],
            "term": "",
            "days": 1,
        },
    }
    return {"data": base64.b64encode(json.dumps(filters, separators=(",", ":")).encode()).decode()}


async def collect(
    client: httpx.AsyncClient,
    *,
    window_start: datetime,
    window_end: datetime,
    library: str,
) -> SourceCollection:
    if library not in CALENDARS:
        raise ValueError("Unsupported public library calendar")
    base = CALENDARS[library]
    # The event listing itself loads this public location metadata to construct
    # branch filters. No credentials or event-registration endpoints are used.
    data = await fetch_bytes(client, f"https://api.communico.co/v1/{library}/locations")
    locations: object = json.loads(data)
    if not isinstance(locations, list) or not 1 <= len(locations) <= 100:
        raise ValueError("Unexpected public library location list")
    branches: dict[str, str] = {}
    for location in locations:
        if not isinstance(location, dict):
            raise ValueError("Invalid public library location")
        identity, name = str(location.get("id", "")), str(location.get("name", ""))
        if not identity.isdecimal() or not name or identity in branches:
            raise ValueError("Missing or repeated library location identity")
        branches[identity] = name
    result = SourceCollection(events=[], records_seen=0, requests=1, issues=[])
    merged: dict[str, ParsedEvent] = {}
    conflicts: set[str] = set()
    # All-events also catches an event whose location has not reached metadata yet.
    for location_id, name in {"all": "All locations", **branches}.items():
        await asyncio.sleep(1)
        result.requests += 1
        try:
            data = await fetch_bytes(client, f"{base}/feeds", params=_feed_params(location_id))
            parsed = parse_feed(
                data,
                fallback_url=f"{base}/events",
                event_base_url=f"{base}/event/",
                midnight_all_day=True,
            )
        except (ValueError, httpx.HTTPError, TimeoutError) as exc:
            result.issues.append(
                ParseIssue(None, f"{name}: download failed ({type(exc).__name__})")
            )
            continue
        result.records_seen += parsed.records_seen
        for issue in parsed.issues:
            if issue not in result.issues:
                result.issues.append(issue)
        if location_id != "all" and parsed.records_seen >= _FEED_LIMIT:
            result.coverage_complete = False
            result.warnings.append(
                f"{name} reached its 500-event export cap; coverage may be partial"
            )
        for event in parsed.events:
            previous = merged.get(event.external_id)
            if previous is not None and previous.content != event.content:
                conflicts.add(event.external_id)
                continue
            feed_url = str(httpx.URL(f"{base}/feeds", params=_feed_params(location_id)))
            prior_urls = previous.raw_payload.get("feed_urls", []) if previous else []
            urls = (
                [value for value in prior_urls if isinstance(value, str)]
                if isinstance(prior_urls, list)
                else []
            )
            feed_urls: list[JsonValue] = list(sorted(set([*urls, feed_url])))
            merged[event.external_id] = replace(
                event,
                raw_payload={**event.raw_payload, "feed_urls": feed_urls},
            )
    result.issues.extend(
        ParseIssue(identity, "Conflicting versions across branch subscriptions")
        for identity in sorted(conflicts)
    )
    result.events = [event for identity, event in merged.items() if identity not in conflicts]
    return result
