"""Connections for server-side catalog access; opening a connection is explicit."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.settings import Settings


@asynccontextmanager
async def connect_database(settings: Settings) -> AsyncGenerator[AsyncConnection[dict[str, Any]]]:
    """Give one caller an idle connection; EventStore owns write transactions."""

    if settings.database_url is None or not settings.database_url.get_secret_value().strip():
        raise RuntimeError("Set EVENTSCOUT_DATABASE_URL before using event storage.")

    async with await AsyncConnection[dict[str, Any]].connect(
        settings.database_url.get_secret_value(),
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=5,
    ) as connection:
        yield connection
