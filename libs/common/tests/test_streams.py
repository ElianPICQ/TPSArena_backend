import asyncio
import uuid

import pytest
from redis.exceptions import ConnectionError
from testcontainers.community.redis import RedisContainer

from tpsarena_common.events import UserDeleted, encode, publish
from tpsarena_common.streams import StreamConsumer, consumer_redis

STREAM = UserDeleted.STREAM


@pytest.fixture(scope="session")
def redis_url():
    with RedisContainer("redis:7") as container:
        yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"


@pytest.fixture
async def redis(redis_url):
    client = consumer_redis(redis_url, block_ms=100)
    await client.flushdb()
    yield client
    await client.aclose()


def make_consumer(redis, handler, **kwargs) -> StreamConsumer:
    return StreamConsumer(redis, "test", "test-group", [STREAM], handler, consumer="c1", block_ms=100, **kwargs)


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
    assert await redis.exists(f"test:handled:test-group:{e.event_id}")


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


async def test_survives_a_lost_connection(redis):
    seen = []

    async def handler(e):
        seen.append(e)

    consumer = make_consumer(redis, handler, retry_delay=0.01)
    real_poll = consumer.poll_once
    failures = 2

    async def flaky_poll():
        nonlocal failures
        if failures:
            failures -= 1
            raise ConnectionError("Connection closed by server.")
        await real_poll()

    consumer.poll_once = flaky_poll
    e = UserDeleted(user_id=uuid.uuid4())
    await publish(redis, e)
    task = asyncio.create_task(consumer.run())
    try:
        await wait_for(lambda: seen == [e])
    finally:
        task.cancel()
    assert failures == 0


async def test_recreates_a_lost_group(redis):
    seen = []

    async def handler(e):
        seen.append(e)

    consumer = make_consumer(redis, handler, retry_delay=0.01)
    task = asyncio.create_task(consumer.run())
    try:
        await wait_for(lambda: redis.exists(STREAM))
        await redis.delete(STREAM)  # the group goes with it, like a Redis that lost its data
        e = UserDeleted(user_id=uuid.uuid4())
        await publish(redis, e)
        await wait_for(lambda: seen == [e])
    finally:
        task.cancel()


async def wait_for(condition, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while True:
            result = condition()
            if asyncio.iscoroutine(result):
                result = await result
            if result:
                return
            await asyncio.sleep(0.02)
