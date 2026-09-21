"""Bound public downloads and revalidate source-specific response bodies."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

_DOWNLOAD_LIMIT = 20 * 1024 * 1024
_CACHE_LIMIT = 32 * 1024 * 1024


@dataclass
class CacheEntry:
    body: bytes
    etag: str | None
    last_modified: str | None
    checked_at: datetime


@dataclass
class ResponseCache:
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    requests: int = 0
    not_modified: int = 0

    def __post_init__(self) -> None:
        self.prune()

    def prune(self) -> None:
        size = sum(len(entry.body) for entry in self.entries.values())
        for url in sorted(self.entries, key=lambda url: self.entries[url].checked_at):
            if len(self.entries) <= 128 and size <= _CACHE_LIMIT:
                break
            size -= len(self.entries.pop(url).body)


_response_cache: ContextVar[ResponseCache | None] = ContextVar("response_cache", default=None)


@contextmanager
def use_cache(cache: ResponseCache) -> Iterator[None]:
    token = _response_cache.set(cache)
    try:
        yield
    finally:
        _response_cache.reset(token)


def _store(cache: ResponseCache, url: str, entry: CacheEntry, headers: httpx.Headers) -> None:
    directives = {
        part.split("=", 1)[0].strip().lower()
        for part in headers.get_list("cache-control", split_commas=True)
    }
    vary = {part.lower() for part in headers.get_list("vary", split_commas=True)}
    if (
        "no-store" in directives
        or vary - {"accept-encoding"}
        or not (entry.etag or entry.last_modified)
    ):
        cache.entries.pop(url, None)
        return
    cache.entries[url] = entry
    cache.prune()


async def fetch_bytes(
    client: httpx.AsyncClient, url: str, *, params: dict[str, str] | None = None
) -> bytes:
    cache = _response_cache.get()
    key = str(client.build_request("GET", url, params=params).url.copy_with(fragment=None))
    cached = cache.entries.get(key) if cache else None
    headers = {}
    if cached:
        if cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
    if not headers:
        cached = None

    async with asyncio.timeout(60):
        for attempt in range(2):
            if cache:
                cache.requests += 1
            async with client.stream("GET", url, params=params, headers=headers) as response:
                if response.history and cache:
                    cache.entries.pop(key, None)
                    cached = None
                if response.status_code == 304:
                    if cached is None or cache is None:
                        if attempt == 0:
                            headers = {}
                            continue
                        raise ValueError("received 304 without a stored response body")
                    cache.not_modified += 1
                    entry = CacheEntry(
                        cached.body,
                        response.headers.get("etag", cached.etag),
                        response.headers.get("last-modified", cached.last_modified),
                        datetime.now(UTC),
                    )
                    _store(cache, key, entry, response.headers)
                    return entry.body
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _DOWNLOAD_LIMIT:
                        raise ValueError("response exceeds the 20 MiB download limit")
                body = bytes(data)
                if cache and response.status_code == 200 and not response.history:
                    _store(
                        cache,
                        key,
                        CacheEntry(
                            body,
                            response.headers.get("etag"),
                            response.headers.get("last-modified"),
                            datetime.now(UTC),
                        ),
                        response.headers,
                    )
                return body
    raise RuntimeError("download did not produce a response")
