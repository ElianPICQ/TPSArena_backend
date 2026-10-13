import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tpsarena_common.jwt_auth import TokenError, token_error_handler

from serverlist.config import get_settings
from serverlist.errors import APIError, api_error_handler
from serverlist.redis_client import redis
from serverlist.routes import router, sweep

log = logging.getLogger("serverlist.sweeper")


async def sweep_forever() -> None:
    """Every instance runs it; the removal script makes sure only one publishes each server.removed."""
    while True:
        try:
            await sweep()
        except Exception:
            log.exception("Sweep failed")
        await asyncio.sleep(get_settings().sweep_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    sweeper = asyncio.create_task(sweep_forever())
    yield
    sweeper.cancel()
    await redis.aclose()


app = FastAPI(title="TPSArena Server List API", lifespan=lifespan)
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(TokenError, token_error_handler)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
