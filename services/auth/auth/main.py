from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from auth import security
from auth.db import engine
from auth.errors import APIError, api_error_handler
from auth.redis_client import redis
from auth.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="TPSArena Auth API", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/.well-known/jwks.json")
async def jwks(response: Response) -> dict:
    # Services cache it; a token with an unknown kid makes them fetch it again.
    response.headers["cache-control"] = "public, max-age=300"
    return security.jwks()
