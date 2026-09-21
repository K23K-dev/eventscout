"""Revisit known occurrences without recrawling whole calendars or inventing cancellations."""

import json
from dataclasses import replace
from datetime import date, datetime
from functools import partial
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.ingestion import (
    carbonhouse,
    chambermaster,
    civic,
    communico,
    gatech,
    library,
    localist,
    tag,
    tech_village,
    tribe,
    trumba,
)
from app.ingestion.http import fetch_bytes
from app.ingestion.icalendar_feed import calendar_events
from app.ingestion.parsing import issue_message
from app.ingestion.records import ParsedEvent, ParseIssue
from app.ingestion.sources import CalendarSource


def supported_recheck(source: CalendarSource) -> bool:
    collect = source.collect.func if isinstance(source.collect, partial) else source.collect
    return collect in {
        tribe.collect,
        localist.collect,
        trumba.collect,
        library.collect,
        carbonhouse.collect,
        chambermaster.collect,
        civic.collect,
        communico.collect,
        gatech.collect,
        tag.collect,
        tech_village.collect,
    }


def _export(html: bytes, page_url: str, marker: str) -> str:
    for link in BeautifulSoup(html, "html.parser").select("a[href]"):
        url = urljoin(page_url, str(link.get("href")))
        if marker in url and urlsplit(url).hostname == urlsplit(page_url).hostname:
            return url
    raise ValueError("Public event export was not present")


async def recheck_event(
    client: httpx.AsyncClient,
    source: CalendarSource,
    previous: ParsedEvent,
    *,
    now: datetime,
) -> ParsedEvent | ParseIssue:
    """Only a freshly parsed, matching occurrence confirms an event remains published."""
    identity = previous.external_id
    if not supported_recheck(source):
        return ParseIssue(identity, "No supported individual-event recheck; awaiting its feed")
    collect = source.collect.func if isinstance(source.collect, partial) else source.collect
    options = source.collect.keywords if isinstance(source.collect, partial) else {}
    raw = previous.raw_payload
    url = str(raw.get("url") or raw.get("link") or previous.content.source_url)
    events: list[ParsedEvent] = []
    try:
        if collect == gatech.collect:
            mercury_id = str(raw.get("mercury_id", ""))
            if not mercury_id.isdecimal():
                raise ValueError("Missing stable Mercury event identity")
            events = [
                gatech.parse_detail(
                    await fetch_bytes(client, f"https://hg.gatech.edu/node/{mercury_id}/json"),
                    previous,
                )
            ]
        elif collect == trumba.collect:
            feed_url = str(httpx.URL(trumba.FEED_URL, params={"eventid": identity}))
            events = trumba.parse_feed(
                await fetch_bytes(client, feed_url), feed_url=feed_url
            ).events
        else:
            if urlsplit(url).hostname != source.config.url.host:
                raise ValueError("Stored event link does not belong to its source")
            if collect == tribe.collect:
                api_url = options["api_url"]
                data = json.loads(await fetch_bytes(client, f"{api_url}/{identity}"))
                events = [tribe._parse(data, api_url)]
            else:
                html = await fetch_bytes(client, url)
                if collect == localist.collect:
                    export = _export(html, url, ".ics")
                    parsed, _ = localist.parse_feed(
                        await fetch_bytes(client, export),
                        publisher_host=str(source.config.url.host),
                    )
                    events = parsed.events
                elif collect == library.collect:
                    # Old listing venue text is not fresh evidence of the current location.
                    events = [library._parse_detail(html, url, "")]
                elif collect == carbonhouse.collect:
                    events, _ = carbonhouse._parse_detail(
                        html, url, options["calendar_url"], options["default_venue"], now
                    )
                elif collect == tech_village.collect:
                    canonical = BeautifulSoup(html, "html.parser").select_one(
                        'link[rel="canonical"]'
                    )
                    if canonical is None or str(canonical.get("href", "")).rstrip(
                        "/"
                    ) != url.rstrip("/"):
                        raise ValueError("Event page no longer matches the stored event link")
                    listing = tech_village._Listing(
                        identity, url, date.fromisoformat(str(raw["listing_date"]))
                    )
                    events = [tech_village._parse_detail(html, listing)]
                elif collect == communico.collect:
                    events = [communico.parse_detail(html, url)]
                elif collect == civic.collect:
                    export = _export(html, url, "/common/modules/iCalendar/")
                    base, _ = civic.CALENDARS[options["city"]]
                    events = [
                        civic._detail(html, civic._parse(component, base, export))
                        for component in calendar_events(await fetch_bytes(client, export))
                        if str(component.get("UID", "")) == identity
                    ]
                elif collect == tag.collect:
                    data, export, registration = tag._detail(html)
                    if not urlsplit(export).path.endswith(f"-{identity}.ics"):
                        raise ValueError("Export does not match the event identity")
                    event = tag._parse(
                        tag._Listing(identity, url),
                        data,
                        await fetch_bytes(client, export),
                        registration,
                    )
                    events = [event] if event else []
                elif collect == chambermaster.collect:
                    canonical = BeautifulSoup(html, "html.parser").select_one(
                        'link[rel="canonical"]'
                    )
                    if canonical is None or not urlsplit(
                        str(canonical.get("href", ""))
                    ).path.endswith(f"-{identity}"):
                        raise ValueError("Event page no longer matches the stored event identity")
                    node, chamber_export = chambermaster._detail(html)
                    ical = None
                    if chamber_export:
                        chamber_export = chambermaster._event_url(
                            options["calendar_url"], chamber_export, path="/events/addtocalendar/"
                        )
                        if not urlsplit(chamber_export).path.endswith(f"-{identity}"):
                            raise ValueError("Export does not match the event identity")
                        ical = await fetch_bytes(client, chamber_export + "?format=ICal")
                    event = chambermaster._parse(
                        chambermaster._Listing(identity, url, ""),
                        node,
                        calendar_url=options["calendar_url"],
                        publisher=options["publisher"],
                        ical=ical,
                    )
                    events = [event] if event else []
        matches = [event for event in events if event.external_id == identity]
        if len(matches) != 1:
            raise ValueError("The known occurrence could not be confirmed in its event details")
        event = matches[0]
        return replace(
            event,
            raw_payload={**event.raw_payload, "feed_urls": raw.get("feed_urls", [])},
        )
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {404, 410}:
            return ParseIssue(identity, "Event page is missing; cancellation is unconfirmed")
        return ParseIssue(identity, f"Event recheck failed: {issue_message(exc)}")
