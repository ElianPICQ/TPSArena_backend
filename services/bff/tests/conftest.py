import httpx
import pytest

from bff.config import get_settings
from bff.main import app


@pytest.fixture
async def client():
    settings = get_settings()
    async with (
        httpx.AsyncClient(base_url=settings.auth_url) as auth_client,
        httpx.AsyncClient(base_url=settings.serverlist_url) as serverlist_client,
        httpx.AsyncClient(base_url=settings.social_url) as social_client,
        httpx.AsyncClient(base_url=settings.stats_url) as stats_client,
    ):
        app.state.auth_client = auth_client
        app.state.serverlist_client = serverlist_client
        app.state.social_client = social_client
        app.state.stats_client = stats_client
        transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
