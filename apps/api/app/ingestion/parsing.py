"""Parsing rules shared by the publisher-specific adapters."""

import re
from datetime import UTC, datetime
from html import unescape
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl, ValidationError

LocationKind = Literal["in_person", "online", "hybrid", "unknown"]
PriceStatus = Literal["free", "paid", "conditional", "unknown"]

MEMBERS_ONLY = re.compile(r"\bmembers?[- ]only\b", re.I)
CANCELLED_TITLE = re.compile(r"^[\s*\[(]*(?:cancelled|canceled)\b", re.I)
REGISTRATION = re.compile(r"\b(?:register|registration|rsvp|tickets?|sign[ -]?up)\b", re.I)

_PLACEHOLDER_VENUE = re.compile(
    r"(?:location:\s*)?(?:tba|tbd|to be (?:announced|determined)|location tbd|n/a"
    r"|see description|related link|various locations)",
    re.I,
)
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


def text(value: object) -> str:
    """Visible text of an HTML string or element, entities decoded and whitespace collapsed.

    Scripts and styles are dropped from HTML strings; elements are read as they are, untouched.
    """
    if isinstance(value, str):
        soup = BeautifulSoup(value, "html.parser")
        for node in soup.select("script, style"):
            node.decompose()
        value = soup
    if not isinstance(value, Tag):
        return ""
    return " ".join(unescape(value.get_text(" ", strip=True)).split())


def location_kind(venue: str | None) -> LocationKind:
    """How to attend, from a venue line: in person for a named place, online or hybrid when it
    says so. A place that also mentions a video link ("Room 101 / Zoom") stays unknown."""
    venue = (venue or "").strip()
    if not venue or _PLACEHOLDER_VENUE.fullmatch(venue):
        return "unknown"
    if re.search(r"\bhybrid\b", venue, re.I):
        return "hybrid"
    if re.match(r"(?:online|virtual|zoom|webinar|microsoft teams)\b", venue, re.I) or re.search(
        r"\b(?:online|virtual) (?:event|session|meeting|program|class|workshop)\b", venue, re.I
    ):
        return "online"
    if re.search(r"\b(?:online|virtual|zoom|webinar)\b", venue, re.I):
        return "unknown"
    return "in_person"


def cost_price(cost: str | None) -> tuple[PriceStatus, str | None]:
    """Classify a published cost: amounts are paid, free wording is free unless qualified
    ("free for members"), and free alongside a price is conditional."""
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


def registration_link(scope: Tag | None, base: str) -> str | None:
    """The first link whose text offers registration or tickets, as an absolute web address."""
    for anchor in scope.select("a[href]") if scope else []:
        if REGISTRATION.search(text(anchor)):
            target = urljoin(base, str(anchor["href"]))
            if urlsplit(target).scheme in {"http", "https"}:
                return target
    return None


def http_url(value: str) -> HttpUrl:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        raise ValueError("Event links must be HTTP(S) URLs without embedded credentials")
    return HttpUrl(value)


def publisher_url(value: str, base: str, path: str) -> str:
    parts = urlsplit(urljoin(base, value))
    if (
        parts.scheme != "https"
        or parts.hostname != urlsplit(base).hostname
        or parts.username
        or parts.password
        or not parts.path.startswith(path)
    ):
        raise ValueError("Expected a link on the publisher's public calendar")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def localize(naive: datetime, zone: ZoneInfo) -> datetime:
    """Attach a timezone only to an unambiguous, existent local wall time."""
    local = naive.replace(tzinfo=zone)
    if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValueError("Nonexistent local event time")
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError("Ambiguous local event time")
    return local


def validation_message(exc: ValidationError) -> str:
    fields = ", ".join(
        ".".join(str(part) for part in error["loc"]) or "URL"
        for error in exc.errors(include_input=False)
    )
    return f"Invalid event fields: {fields}"


def issue_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, ValidationError):
        return "Invalid event fields"
    return str(exc) if isinstance(exc, ValueError) else type(exc).__name__
