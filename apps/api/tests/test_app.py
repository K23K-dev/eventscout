import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from eventscout_api.main import create_app
from eventscout_api.settings import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_is_available_without_external_services() -> None:
    transport = ASGITransport(app=create_app(Settings(_env_file=None)))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_configured_browser_origin_can_read_health() -> None:
    settings = Settings(cors_origins=["https://eventscout.example"], _env_file=None)
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health", headers={"Origin": "https://eventscout.example"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://eventscout.example"


@pytest.mark.anyio
async def test_unlisted_browser_origin_does_not_receive_cors_permission() -> None:
    settings = Settings(cors_origins=["https://eventscout.example"], _env_file=None)
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health", headers={"Origin": "https://unlisted.example"})

    assert "access-control-allow-origin" not in response.headers


def test_environment_can_override_browser_origins(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTSCOUT_CORS_ORIGINS", '["https://eventscout.example"]')

    assert Settings(_env_file=None).cors_origins == ["https://eventscout.example"]
