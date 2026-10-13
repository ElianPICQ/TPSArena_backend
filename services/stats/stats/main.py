from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tpsarena_common.jwt_auth import TokenError, token_error_handler

from stats.db import engine
from stats.errors import APIError, api_error_handler
from stats.redis_client import redis
from stats.routes import leaderboards, matches, players


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="TPSArena Stats API", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(TokenError, token_error_handler)
app.include_router(matches.router)
app.include_router(players.router)
app.include_router(leaderboards.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
