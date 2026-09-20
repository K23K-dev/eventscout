"""Small parsing primitives shared by the publisher-specific adapters."""

from datetime import UTC, datetime
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl, ValidationError


def node_text(node: Tag | None, *, decode_entities: bool = False) -> str:
    value = node.get_text(" ", strip=True) if node else ""
    return " ".join((unescape(value) if decode_entities else value).split())


def html_text(value: object, *, decode_entities: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup.select("script, style"):
        tag.decompose()
    return node_text(soup, decode_entities=decode_entities)


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
