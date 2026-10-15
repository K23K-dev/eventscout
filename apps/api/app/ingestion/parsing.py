"""Parsing rules shared by the publisher-specific adapters."""

import re
from datetime import date, datetime
from html import unescape
from typing import Literal
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
from dateutil import parser
from dateutil.rrule import MONTHLY, rrule
from pydantic import HttpUrl

LocationKind = Literal["in_person", "online", "hybrid", "unknown"]
PriceStatus = Literal["free", "paid", "conditional", "unknown"]

# Every calendar publishes Atlanta wall-clock times.
ATLANTA = ZoneInfo("America/New_York")

MEMBERS_ONLY = re.compile(r"\bmembers?[- ]only\b", re.I)

_PLACEHOLDER_VENUE = re.compile(r"tba|tbd|related link", re.I)
_FREE_WORDING = re.compile(r"\b(?:free|no (?:charge|cost|fee)|complimentary)\b", re.I)
_FREE_PHRASE = re.compile(
    r"\b(?:free(?:[,\s]+(?:statewide|virtual|online)){0,2}\s+"
    r"(?:admission|entry|registration|workshop|event|webinar|seminar)"
    r"|(?:admission|entry|registration|event|program|workshop|session|webinar|conference|seminar"
    r"|tickets?)\s+(?:is|are|will be)\s+free)"
    r"(?P<qualifier>\s+(?:for|to)\s+(?!(?:the public|all|everyone|anyone|attend)\b)[^.!?]+)?",
    re.I,
)
_PAID_PHRASE = re.compile(
    r"\b(?:tickets?|admission|registration)\s*(?:is |are |: )?(?:only )?\$\s*\d+(?:\.\d{2})?", re.I
)


def text(value: object, selector: str | None = None) -> str:
    """Visible text of HTML or an element, or of its first `selector` match, spaces collapsed."""
    if selector is not None and isinstance(value, Tag):
        value = value.select_one(selector)
    if isinstance(value, str):
        value = BeautifulSoup(value, "html.parser")
    if not isinstance(value, Tag):
        return ""
    return " ".join(unescape(value.get_text(" ", strip=True)).split())


def location_kind(venue: str | None) -> LocationKind:
    """In person for a named place, online or hybrid when it says so, otherwise unknown."""
    venue = (venue or "").strip()
    if not venue or _PLACEHOLDER_VENUE.fullmatch(venue):
        return "unknown"
    if re.search(r"\bhybrid\b", venue, re.I):
        return "hybrid"
    if re.match(r"(?:online|virtual|zoom|webinar|microsoft teams)\b", venue, re.I):
        return "online"
    if re.search(r"\b(?:online|virtual|zoom|webinar)\b", venue, re.I):
        return "unknown"
    return "in_person"


def cost_price(cost: str | None) -> tuple[PriceStatus, str | None]:
    """Paid for an amount, free unless qualified ("free for members"), conditional if both."""
    cost = " ".join((cost or "").split())
    value = cost.casefold()
    bare = value.strip(" .!")
    if not bare or bare in {"n/a", "na", "unknown", "tbd", "tba"}:
        return "unknown", None
    amounts = [float(amount) for amount in re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)", value)]
    if not amounts and re.fullmatch(r"\d+(?:\.\d{1,2})?", bare):
        amounts = [float(bare)]
    free = _FREE_WORDING.search(value) is not None
    if any(amount > 0 for amount in amounts):
        return ("conditional" if free or 0 in amounts else "paid"), cost
    if free or amounts:
        qualified = re.search(
            r"\b(?:for|with|members?|students?|donations?|suggested|varies|except)\b", value
        )
        return ("conditional" if qualified else "free"), cost
    if re.search(r"\b(?:varies|donations?|members?|included (?:in|with))\b", value):
        return "conditional", cost
    return "unknown", cost


def described_price(description: str) -> tuple[PriceStatus, str | None]:
    """Admission stated in prose ("admission is free", "tickets are $20"); otherwise unknown."""
    if match := _FREE_PHRASE.search(description):
        return ("conditional" if match["qualifier"] else "free"), match[0]
    if match := _PAID_PHRASE.search(description):
        return "paid", match[0]
    return "unknown", None


def http_url(value: str) -> HttpUrl:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        raise ValueError("Event links must be HTTP(S) URLs without embedded credentials")
    return HttpUrl(value)


def image_url(value: object, base: str = "") -> str | None:
    """An https picture address from a feed: a URL, or an {"url": ...} object."""
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(base, value.strip())
    return url if url.startswith("https://") else None


def local_time(value: str) -> datetime:
    """A date and wall-clock time as a calendar page writes it, such as "Oct 4, 2026 7:30 PM"."""
    return parser.parse(value).replace(tzinfo=ATLANTA)


def months(start: datetime, end: datetime) -> list[date]:
    """The first day of every Atlanta month the window touches, for month-by-month calendars."""
    first = start.astimezone(ATLANTA).date().replace(day=1)
    last = end.astimezone(ATLANTA).date()
    return [day.date() for day in rrule(MONTHLY, dtstart=first, until=last)]
