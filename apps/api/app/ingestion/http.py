"""Bound public downloads in time and size."""

import asyncio

import httpx

_DOWNLOAD_LIMIT = 20 * 1024 * 1024


async def fetch_bytes(
    client: httpx.AsyncClient, url: str, *, params: dict[str, str] | None = None
) -> bytes:
    async with asyncio.timeout(60), client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > _DOWNLOAD_LIMIT:
                raise ValueError("response exceeds the 20 MiB download limit")
        return bytes(data)


async def fetch_pages(client: httpx.AsyncClient, urls: list[str]) -> list[bytes | BaseException]:
    """Download pages two at a time, 0.5 s apart; a failed download returns its exception."""
    semaphore = asyncio.Semaphore(2)

    async def fetch(url: str) -> bytes:
        async with semaphore:
            await asyncio.sleep(0.5)
            return await fetch_bytes(client, url)

    return await asyncio.gather(*(fetch(url) for url in urls), return_exceptions=True)
