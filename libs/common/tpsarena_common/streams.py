"""Base for the Redis Streams consumers (run as their own process, e.g. `python -m social.worker`).

- One consumer group per consuming service; instances of a service share it.
- XACK after handling. A failed event stays pending and is retried: XAUTOCLAIM takes over
  messages pending for longer than `claim_idle_ms` (from crashed consumers or failed handlers).
- Delivery is at least once: event ids already handled are skipped. The "handled" markers are
  stored under the service's own key prefix (e.g. `social:handled:...`), like all its keys.
- Events that can't be decoded are logged and acknowledged, so they don't block the group.
"""

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from tpsarena_common.events import Event, UnknownEvent, decode

log = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]


def consumer_redis(url: str, block_ms: int = 5000) -> Redis:
    """A client whose socket timeout outlasts the blocking XREADGROUP."""
    return Redis.from_url(url, decode_responses=True, socket_timeout=block_ms / 1000 + 10)


class StreamConsumer:
    def __init__(
        self,
        redis: Redis,
        key_prefix: str,
        group: str,
        streams: list[str],
        handler: Handler,
        consumer: str | None = None,
        block_ms: int = 5000,
        claim_idle_ms: int = 60_000,
        handled_ttl_seconds: int = 7 * 24 * 3600,
    ):
        self.redis = redis
        self.key_prefix = key_prefix
        self.group = group
        self.streams = streams
        self.handler = handler
        self.consumer = consumer or socket.gethostname()
        self.block_ms = block_ms
        self.claim_idle_ms = claim_idle_ms
        self.handled_ttl_seconds = handled_ttl_seconds

    async def ensure_groups(self) -> None:
        for stream in self.streams:
            try:
                await self.redis.xgroup_create(stream, self.group, id="0", mkstream=True)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def handle(self, stream: str, message_id: str, fields: dict[str, str]) -> None:
        try:
            e = decode(fields)
        except (UnknownEvent, ValidationError, KeyError, ValueError) as exc:
            log.error("Dropping undecodable message %s on %s: %r", message_id, stream, exc)
            await self.redis.xack(stream, self.group, message_id)
            return

        handled_key = f"{self.key_prefix}:handled:{self.group}:{e.event_id}"
        if not await self.redis.exists(handled_key):
            try:
                await self.handler(e)
            except Exception:
                log.exception("Handler failed for %s %s, will retry", e.TYPE, message_id)
                return
            await self.redis.set(handled_key, 1, ex=self.handled_ttl_seconds)
        await self.redis.xack(stream, self.group, message_id)

    async def reclaim(self) -> None:
        for stream in self.streams:
            start = "0-0"
            while True:
                start, messages, _ = await self.redis.xautoclaim(
                    stream, self.group, self.consumer, self.claim_idle_ms, start_id=start
                )
                for message_id, fields in messages:
                    if fields:
                        await self.handle(stream, message_id, fields)
                if start == "0-0":
                    break

    async def poll_once(self) -> None:
        response = await self.redis.xreadgroup(
            self.group, self.consumer, {s: ">" for s in self.streams}, count=10, block=self.block_ms
        )
        for stream, messages in response or []:
            for message_id, fields in messages:
                await self.handle(stream, message_id, fields)

    async def run(self) -> None:
        await self.ensure_groups()
        log.info("Consuming %s as %s/%s", ", ".join(self.streams), self.group, self.consumer)
        loop = asyncio.get_running_loop()
        last_reclaim = float("-inf")
        while True:
            if loop.time() - last_reclaim > self.claim_idle_ms / 1000:
                await self.reclaim()
                last_reclaim = loop.time()
            await self.poll_once()
