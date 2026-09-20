"""Application entry point; startup requires no external services."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.events.router import create_events_router
from app.ingestion.sources import SOURCES
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    application = FastAPI(title="EventScout", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET"],
    )

    @application.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(
        create_events_router(
            config, tuple(slug for slug, source in SOURCES.items() if source.enabled)
        )
    )
    return application


app = create_app()
