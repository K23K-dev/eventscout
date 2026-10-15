"""Read the GT Library's paginated public calendar and stable Drupal event pages."""

import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from dateutil import parser
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes, fetch_pages
from app.ingestion.parsing import described_price, http_url, local_time, location_kind, text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection, issue_message
from app.storage.models import EventContent

LISTING_URL = "https://library.gatech.edu/events-workshops"
_MAX_PAGES = 50


def _url(value: str, *, library_only: bool = False) -> str:
    absolute = urljoin(LISTING_URL, value)
    url = http_url(absolute)
    if library_only and urlsplit(absolute).hostname != "library.gatech.edu":
        raise ValueError("Library calendar link unexpectedly leaves its publisher")
    return str(url)


def _href(tag: Tag | None) -> str:
    if tag is None or not isinstance(value := tag.get("href"), str) or not value:
        raise ValueError("Missing event link")
    return value


def _parse_detail(html: bytes, url: str, listing_venue: str) -> ParsedEvent:
    soup = BeautifulSoup(html, "html.parser")
    shortlink = _url(_href(soup.select_one('link[rel="shortlink"]')), library_only=True)
    node = re.fullmatch(r"/node/(\d+)", urlsplit(shortlink).path)
    if node is None:
        raise ValueError("Missing stable Drupal node ID")
    detail = soup.select_one("main .node-detail")
    if detail is None:
        raise ValueError("Missing Library event details")
    title = text(detail, "h1")
    day = " ".join(text(detail, f".node-detail__{part}") for part in ("day", "month", "year"))
    clock = text(detail, ".hours-of-operation")
    # Library pages publish campus-local wall times, without a UTC offset.
    all_day = bool(re.fullmatch(r"all[ -]day", clock, re.I))
    start_date = parser.parse(day).date() if all_day else None
    starts_at = ends_at = None
    if not all_day:
        clocks = re.split(r"\s*[-–—]\s*", clock)
        if len(clocks) not in {1, 2}:
            raise ValueError("Expected one start time and an optional end time")
        starts_at = local_time(f"{day} {clocks[0]}")
        ends_at = local_time(f"{day} {clocks[1]}") if len(clocks) == 2 else None
    description = text(detail, ".node-detail__content-body")
    detail_venue = text(detail, ".taxonomy-box.location")
    venue = listing_venue or detail_venue
    if venue.casefold() == "in person":
        venue = ""
    register = detail.select_one("a.register-button")
    category = text(detail, ".node-detail__category")
    price_status, price_details = described_price(description)
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        start_date=start_date,
        end_date=start_date + timedelta(days=1) if start_date is not None else None,
        venue=venue or None,
        location_kind=location_kind(venue or detail_venue),
        region="gt",
        price_status=price_status,
        price_details=price_details,
        tags=[category] if category else [],
        source_url=HttpUrl(url),
        registration_url=HttpUrl(_url(_href(register))) if register is not None else None,
    )
    return ParsedEvent(f"node:{node[1]}", content)


async def collect(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Follow the listing's pages, then read each event's detail page."""
    result = SourceCollection()
    listings: dict[str, str] = {}
    visited: set[str] = set()
    next_url: str | None = LISTING_URL
    while next_url is not None and len(visited) < _MAX_PAGES:
        if next_url in visited:
            result.issues.append(ParseIssue(None, "Library pagination repeats an earlier page"))
            break
        visited.add(next_url)
        try:
            html = await fetch_bytes(client, next_url)
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".public-programming-listing--view a.event-card")
            result.records_seen += len(cards)
            for card in cards:
                url = _url(_href(card), library_only=True)
                if not urlsplit(url).path.startswith("/events/"):
                    raise ValueError("Unexpected Library event detail path")
                listings.setdefault(url, text(card, ".event-card__class_type"))
            next_links = {
                _url(_href(tag), library_only=True)
                for tag in soup.select('.public-programming-listing--view a[rel="next"]')
            }
            if len(next_links) > 1:
                raise ValueError("Library pagination has conflicting next links")
            next_url = next(iter(next_links), None)
            if next_url and urlsplit(next_url).path != "/events-workshops":
                raise ValueError("Unexpected Library pagination path")
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(ParseIssue(None, f"Library listing failed: {issue_message(exc)}"))
            break
    else:
        if next_url is not None:
            result.issues.append(ParseIssue(None, "Library pagination exceeded the 50-page limit"))
    urls = list(listings)
    for url, page in zip(urls, await fetch_pages(client, urls), strict=True):
        with result.issue_if_fails(url):
            if isinstance(page, BaseException):
                raise page
            result.events.append(_parse_detail(page, url, listings[url]))
    return result
