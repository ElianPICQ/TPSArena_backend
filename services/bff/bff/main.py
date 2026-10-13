from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from bff.config import get_settings
from bff.routes import auth, servers, social, stats


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    timeout = settings.upstream_timeout_seconds
    async with (
        httpx.AsyncClient(base_url=settings.auth_url, timeout=timeout) as auth_client,
        httpx.AsyncClient(base_url=settings.serverlist_url, timeout=timeout) as serverlist_client,
        httpx.AsyncClient(base_url=settings.social_url, timeout=timeout) as social_client,
        httpx.AsyncClient(base_url=settings.stats_url, timeout=timeout) as stats_client,
    ):
        app.state.auth_client = auth_client
        app.state.serverlist_client = serverlist_client
        app.state.social_client = social_client
        app.state.stats_client = stats_client
        yield


app = FastAPI(title="TPSArena BFF", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(social.router)
app.include_router(stats.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
