"""Official Atlanta concert and museum calendars with published event dates."""

import asyncio
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import (
    CANCELLED_TITLE,
    cost_price,
    http_url,
    issue_message,
    localize,
    registration_link,
)
from app.ingestion.parsing import text as _text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

_ZONE = ZoneInfo("America/New_York")
_EARL = "https://badearl.com/"
_FERNBANK = "https://www.fernbankmuseum.org/events/calendar-of-events/"


def _url(value: object, base: str) -> HttpUrl:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing published event link")
    return http_url(urljoin(base, value))


def _local(value: str, pattern: str) -> datetime:
    return localize(datetime.strptime(value, pattern), _ZONE).astimezone(UTC)


def _issue(identity: str | None, exc: Exception) -> ParseIssue:
    return ParseIssue(identity, issue_message(exc))


def _earl_event(card: Tag) -> ParsedEvent:
    identifiers = [
        match.group(1)
        for value in card.get("class", [])
        if (match := re.fullmatch(r"cl-layout__item--id-(\d+)", str(value)))
    ]
    if len(identifiers) != 1:
        raise ValueError("Missing stable EARL event post ID")
    detail = card.select_one(".more-info-btn a")
    source_url = _url(detail.get("href") if detail else None, _EARL)
    date = _text(card.select_one(".show-listing-date")).replace(".", "")
    times = [_text(tag) for tag in card.select(".show-listing-time")]
    show_times = [value for value in times if re.search(r"\bshow\b", value, re.I)]
    if len(show_times) != 1:
        raise ValueError("Missing unambiguous show time (door times are not event starts)")
    clock = re.sub(r"\s*show\s*$", "", show_times[0], flags=re.I).replace(" ", "")
    starts_at = _local(f"{date} {clock}", "%A, %b %d, %Y %I:%M%p")
    title = _text(card.select_one(".show-listing-headliner"))
    if not title:
        title = _text(card.select_one(".show-listing-title-contain"))
    support = [_text(tag) for tag in card.select(".show-listing-support") if _text(tag)]
    prices = [_text(tag) for tag in card.select(".show-listing-price") if _text(tag)]
    free = _text(card.select_one(".listing-free-show-contain"))
    price_status, cost = cost_price(" | ".join(prices) or free)
    ticket = card.select_one(".cl-element-custom_field.show-btn a")
    registration = _url(ticket.get("href"), _EARL) if ticket else None
    description = ". ".join(
        value
        for value in (
            _text(card.select_one(".show-listing-summ-contain")),
            "With " + ", ".join(support) if support else "",
        )
        if value
    )
    content = EventContent(
        title=title,
        description=description,
        starts_at=starts_at,
        venue="The EARL, 488 Flat Shoals Avenue SE, Atlanta, GA 30316",
        location_kind="in_person",
        region="atlanta",
        price_status=price_status,
        price_details=cost,
        tags=["Music"],
        source_url=source_url,
        registration_url=registration,
        status="cancelled" if CANCELLED_TITLE.match(title) else "scheduled",
    )
    return ParsedEvent(
        external_id=identifiers[0],
        content=content,
        source_updated_at=None,
        raw_payload={"post_id": identifiers[0], "listing_html": str(card)},
    )


async def collect_earl(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Read the venue's public show pages with its requested ten-second crawl delay."""
    result = SourceCollection([], 0, [])
    next_url: str | None = _EARL
    visited: set[str] = set()
    seen: set[str] = set()
    while next_url and len(visited) < 20:
        if next_url in visited:
            result.issues.append(ParseIssue(None, "EARL pagination repeats a page"))
            break
        if visited:
            await asyncio.sleep(10)
        visited.add(next_url)
        try:
            soup = BeautifulSoup(await fetch_bytes(client, next_url), "html.parser")
            cards = soup.select(".cl-layout__item")
            if not cards:
                raise ValueError("Missing EARL event cards")
            result.records_seen += len(cards)
            for card in cards:
                try:
                    event = _earl_event(card)
                    if event.external_id in seen:
                        raise ValueError("Repeated EARL event post ID")
                    seen.add(event.external_id)
                    result.events.append(event)
                except (ValueError, TypeError) as exc:
                    result.issues.append(_issue(None, exc))
            next_link = next(
                (
                    tag
                    for tag in soup.select('a[href*="sf_paged="]')
                    if re.match(r"next\b", _text(tag), re.I)
                ),
                None,
            )
            next_url = str(_url(next_link.get("href"), _EARL)) if next_link else None
            if next_url and urlsplit(next_url).hostname != "badearl.com":
                raise ValueError("EARL pagination unexpectedly leaves the publisher")
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            result.issues.append(_issue(None, exc))
            break
    else:
        if next_url:
            result.issues.append(ParseIssue(None, "EARL exceeded the 20-page limit"))
    result.events.sort(key=lambda event: event.external_id)
    return result


def _fernbank_event(html: bytes, url: str, card: Tag) -> ParsedEvent:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article")
    listing_only = article is None
    article = card if article is None else article
    title = _text(article.select_one("h4" if listing_only else "h2"))
    published_time = _text(article.select_one("h5" if listing_only else "h4"))
    match = re.fullmatch(
        r"([A-Za-z]+, [A-Za-z]+ \d{1,2}, \d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)"
        r"(?:\s*[-–—]\s*(\d{1,2}:\d{2}\s*[AP]M))?",
        published_time,
        re.I,
    )
    if not match:
        raise ValueError("Missing explicit Fernbank date and start time")
    day, start, end = match.groups()
    starts_at = _local(f"{day} {start}", "%A, %B %d, %Y %I:%M %p")
    ends_at = _local(f"{day} {end}", "%A, %B %d, %Y %I:%M %p") if end else None
    invalid_end = ends_at is not None and ends_at <= starts_at
    if invalid_end:
        # The publisher sometimes displays 12:00 AM as a placeholder end time.
        ends_at = None
    paragraphs = [_text(tag) for tag in article.select("p") if _text(tag)]
    description = " ".join(paragraphs)
    cost = next(
        (
            text
            for text in paragraphs
            if re.match(r"(?:cost:|included (?:in|with) general admission)", text, re.I)
        ),
        None,
    )
    price_status, price_details = cost_price(cost)
    audience = [
        text for text in paragraphs if re.match(r"(?:recommended for|ages?\b|for ages)", text, re.I)
    ]
    registration = registration_link(article, url)
    location = "" if listing_only else _text(article.select_one("h5"))
    return ParsedEvent(
        external_id=urlsplit(url).path.rstrip("/"),
        content=EventContent(
            title=title,
            description=description,
            starts_at=starts_at,
            ends_at=ends_at,
            venue=", ".join(
                value
                for value in (location, "Fernbank Museum, 767 Clifton Road NE, Atlanta, GA 30307")
                if value
            ),
            location_kind="in_person",
            region="atlanta",
            price_status=price_status,
            price_details=price_details,
            audience=audience,
            tags=["Museum"],
            source_url=HttpUrl(url),
            registration_url=HttpUrl(registration) if registration else None,
            status="cancelled" if CANCELLED_TITLE.match(title) else "scheduled",
        ),
        source_updated_at=None,
        raw_payload={
            "url": url,
            "detail_html": str(article),
            "listing_only": listing_only,
            "invalid_published_end": invalid_end,
        },
    )


async def collect_fernbank(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    """Read the official calendar and its event detail permalinks, two at a time."""
    result = SourceCollection([], 0, [])
    try:
        soup = BeautifulSoup(await fetch_bytes(client, _FERNBANK), "html.parser")
        cards = {
            str(url): anchor.find_parent("div", class_="col-md-4")
            for anchor in soup.select("a[href]")
            if re.fullmatch(
                r"/events/calendar-of-events/\d{4}/\d{2}/[^/]+/", str(anchor.get("href"))
            )
            and (url := _url(anchor.get("href"), _FERNBANK))
        }
        if not cards or any(card is None for card in cards.values()):
            raise ValueError("Missing Fernbank calendar event links")
        if len(cards) > 300:
            raise ValueError("Fernbank calendar exceeds the 300-detail limit")
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        result.issues.append(_issue(None, exc))
        return result
    result.records_seen = len(cards)
    semaphore = asyncio.Semaphore(2)

    async def detail(url: str, card: Tag | None) -> ParsedEvent | ParseIssue:
        async with semaphore:
            try:
                assert card is not None
                return _fernbank_event(await fetch_bytes(client, url), url, card)
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError) as exc:
                return _issue(url, exc)

    for event in await asyncio.gather(*(detail(url, card) for url, card in sorted(cards.items()))):
        if isinstance(event, ParseIssue):
            result.issues.append(event)
        else:
            result.events.append(event)
            if event.raw_payload["invalid_published_end"]:
                result.warnings.append(
                    f"{event.content.title}: invalid published end retained as unknown."
                )
            if event.raw_payload["listing_only"]:
                result.warnings.append(
                    f"{event.content.title}: used dated listing after redirect to a series."
                )
    result.events.sort(key=lambda event: event.external_id)
    return result
