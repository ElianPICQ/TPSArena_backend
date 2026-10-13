"""Needs Redis from docker compose. Uses database 13."""

import uuid

import pytest

from tpsarena_common.events import UserDeleted, encode, publish
from tpsarena_common.streams import StreamConsumer, consumer_redis

STREAM = UserDeleted.STREAM


@pytest.fixture
async def redis():
    client = consumer_redis("redis://localhost:6379/13", block_ms=100)
    await client.flushdb()
    yield client
    await client.aclose()


def make_consumer(redis, handler, **kwargs) -> StreamConsumer:
    return StreamConsumer(redis, "test-group", [STREAM], handler, consumer="c1", block_ms=100, **kwargs)


async def test_handles_and_acks(redis):
    seen = []

    async def handler(e):
        seen.append(e)

    consumer = make_consumer(redis, handler)
    await consumer.ensure_groups()
    e = UserDeleted(user_id=uuid.uuid4())
    await publish(redis, e)
    await consumer.poll_once()

    assert seen == [e]
    assert (await redis.xpending(STREAM, "test-group"))["pending"] == 0


async def test_skips_duplicate_event_ids(redis):
    seen = []

    async def handler(e):
        seen.append(e)

    consumer = make_consumer(redis, handler)
    await consumer.ensure_groups()
    e = UserDeleted(user_id=uuid.uuid4())
    await redis.xadd(STREAM, encode(e))
    await redis.xadd(STREAM, encode(e))  # e.g. the publisher retried
    await consumer.poll_once()
    assert seen == [e]


async def test_failed_handler_is_retried_by_reclaim(redis):
    calls = []

    async def flaky(e):
        calls.append(e)
        if len(calls) == 1:
            raise RuntimeError("database down")

    consumer = make_consumer(redis, flaky, claim_idle_ms=0)
    await consumer.ensure_groups()
    await publish(redis, UserDeleted(user_id=uuid.uuid4()))
    await consumer.poll_once()
    assert (await redis.xpending(STREAM, "test-group"))["pending"] == 1

    await consumer.reclaim()
    assert len(calls) == 2
    assert (await redis.xpending(STREAM, "test-group"))["pending"] == 0


async def test_undecodable_message_is_dropped(redis):
    async def handler(e):
        raise AssertionError("must not be called")

    consumer = make_consumer(redis, handler)
    await consumer.ensure_groups()
    await redis.xadd(STREAM, {"event_id": str(uuid.uuid4()), "type": "who.knows", "data": "{}"})
    await consumer.poll_once()
    assert (await redis.xpending(STREAM, "test-group"))["pending"] == 0
