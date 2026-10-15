from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from web_bff.config import get_settings
from web_bff.routes import auth, friends, realtime, stats


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    timeout = settings.upstream_timeout_seconds
    async with (
        httpx.AsyncClient(base_url=settings.auth_url, timeout=timeout) as auth_client,
        httpx.AsyncClient(base_url=settings.social_url, timeout=timeout) as social_client,
        httpx.AsyncClient(base_url=settings.stats_url, timeout=timeout) as stats_client,
        httpx.AsyncClient(base_url=settings.realtime_url, timeout=timeout) as realtime_client,
    ):
        app.state.auth_client = auth_client
        app.state.social_client = social_client
        app.state.stats_client = stats_client
        app.state.realtime_client = realtime_client
        yield


app = FastAPI(title="TPSArena Web BFF", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(stats.router)
app.include_router(friends.router)
app.include_router(realtime.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
