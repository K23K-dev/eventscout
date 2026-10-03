"""Application entry point; startup requires no external services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.assistant.router import AssistantClients, create_assistant_router
from app.events.router import create_events_router
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    clients = AssistantClients(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await clients.close()

    application = FastAPI(title="EventScout", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @application.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(create_events_router(config))
    application.include_router(create_assistant_router(config, clients))
    return application


app = create_app()
