"""Official team schedules, restricted to games played in Atlanta."""

import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.http import fetch_bytes
from app.ingestion.parsing import text
from app.ingestion.records import ParsedEvent, ParseIssue, SourceCollection
from app.storage.models import EventContent

FALCONS_URL = "https://www.atlantafalcons.com/schedule/"


def _text(card: Tag, selector: str) -> str:
    return text(card.select_one(selector))


def _falcons_event(card: Tag) -> ParsedEvent:
    identity = str(UUID(str(card.get("data-gameid", ""))))
    published_time = str(card.get("data-gametime", ""))
    starts_at = datetime.strptime(published_time, "%m/%d/%Y %H:%M:%S %z")
    link = card.select_one('a[aria-label="Game Center"][href]')
    if link is None:
        raise ValueError("Missing published game detail link")
    source_url = urljoin(FALCONS_URL, str(link["href"]))
    if urlsplit(source_url).hostname != "www.atlantafalcons.com":
        raise ValueError("Unexpected game detail host")
    venue = _text(card, ".nfl-o-matchup-cards__venue--location")
    opponent = _text(card, ".nfl-o-matchup-cards__team-full-name")
    if not opponent:
        raise ValueError("Missing opponent")
    date_label = _text(card, ".nfl-o-matchup-cards__date-info")
    ticket = card.select_one(".nfl-o-matchup-cards__btn-buy-tickets[href]")
    ticket_url = HttpUrl(str(ticket["href"])) if ticket else None
    content = EventContent(
        title=f"Atlanta Falcons vs. {opponent}",
        description=f"NFL football at {venue}. {date_label}",
        starts_at=starts_at,
        venue=venue,
        location_kind="in_person",
        region="atlanta",
        tags=["Sports", "Football", "NFL"],
        source_url=HttpUrl(source_url),
        registration_url=ticket_url,
        status="cancelled" if re.search(r"\bcancel(?:led|ed)\b", date_label, re.I) else "scheduled",
    )
    return ParsedEvent(
        identity,
        content,
        None,
        {
            "game_id": identity,
            "game_time": published_time,
            "opponent": opponent,
            "venue": venue,
            "date_label": date_label,
            "feed_urls": [FALCONS_URL],
        },
    )


async def collect_falcons(
    client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
) -> SourceCollection:
    document = BeautifulSoup(await fetch_bytes(client, FALCONS_URL), "html.parser")
    cards = document.select(".nfl-o-matchup-cards[data-gameid][data-gametime]")
    if not cards:
        raise ValueError("Missing official Falcons schedule")
    result = SourceCollection([], len(cards), 1, [])
    for card in cards:
        if _text(card, ".nfl-o-matchup-cards__venue--location") != "Mercedes-Benz Stadium":
            continue
        try:
            date_label = _text(card, ".nfl-o-matchup-cards__date-info")
            if re.search(r"\b(?:TBD|TBA)\b", date_label):
                result.warnings.append("Omitted a game whose kickoff date or time is unannounced")
                continue
            event = _falcons_event(card)
            result.events.append(event)
        except ValueError as exc:
            result.issues.append(ParseIssue(str(card.get("data-gameid")), str(exc)))
    return result
