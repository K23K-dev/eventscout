"""Bound the time and decompressed size of public calendar downloads."""

import asyncio

import httpx


async def fetch_bytes(
    client: httpx.AsyncClient, url: str, *, params: dict[str, str] | None = None
) -> bytes:
    async with asyncio.timeout(60):
        async with client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError("response exceeds the 20 MiB download limit")
            return bytes(data)
