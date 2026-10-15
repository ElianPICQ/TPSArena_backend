from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tpsarena_common.jwt_auth import TokenError, token_error_handler

from social import clients
from social.db import engine
from social.errors import APIError, api_error_handler
from social.redis_client import redis
from social.routes import friends, loadouts, parties, profiles


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await clients.close()
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="TPSArena Social API", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(TokenError, token_error_handler)
app.include_router(profiles.router)
app.include_router(loadouts.router)
app.include_router(friends.router)
app.include_router(parties.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
