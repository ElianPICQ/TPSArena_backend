from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tpsarena_common.jwt_auth import TokenError, token_error_handler

from serverlist.errors import APIError, api_error_handler
from serverlist.redis_client import redis
from serverlist.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await redis.aclose()


app = FastAPI(title="TPSArena Server List API", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(TokenError, token_error_handler)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
