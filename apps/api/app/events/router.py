"""Public catalog endpoints; database access stays lazy and read-only."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import Error as DatabaseError

from app.database import connect_database
from app.events.models import ErrorResponse, EventFilters, EventPage, EventResponse
from app.events.repository import EventRepository
from app.settings import Settings

logger = logging.getLogger(__name__)


def create_events_router(settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/events",
        tags=["events"],
        responses={503: {"model": ErrorResponse, "description": "Catalog temporarily unavailable"}},
    )

    async def repository() -> AsyncGenerator[EventRepository]:
        if settings.database_url is None or not settings.database_url.get_secret_value().strip():
            raise HTTPException(status_code=503, detail="The event catalog is unavailable.")
        try:
            async with (
                asyncio.timeout(8),
                connect_database(settings) as connection,
                connection.transaction(),
            ):
                await connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                await connection.execute("SET LOCAL statement_timeout = '5s'")
                yield EventRepository(connection)
        except (DatabaseError, TimeoutError) as exc:
            logger.warning("Event catalog request failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=503, detail="The event catalog is temporarily unavailable."
            ) from None

    @router.get("", response_model=EventPage, operation_id="listEvents")
    async def list_events(
        filters: Annotated[EventFilters, Query()],
        catalog: Annotated[EventRepository, Depends(repository)],
    ) -> EventPage:
        """Search scheduled events overlapping an Atlanta calendar-date window.

        The default is today through 30 days later (exclusive), including ongoing
        events and earlier events on the first date. Unknown start times are omitted.
        Blank search text browses by date; punctuation/stopword-only searches match
        nothing. Page counts and results share a database snapshot per request.
        Changes to the catalog between requests can shift offset-based pages.
        """
        return await catalog.search(filters)

    @router.get(
        "/{event_id}",
        response_model=EventResponse,
        operation_id="getEvent",
        responses={404: {"model": ErrorResponse, "description": "Event not found"}},
    )
    async def get_event(
        event_id: UUID, catalog: Annotated[EventRepository, Depends(repository)]
    ) -> EventResponse:
        """Get an event with the calendars that list it, including past/cancelled events."""
        event = await catalog.get(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        return event

    return router
