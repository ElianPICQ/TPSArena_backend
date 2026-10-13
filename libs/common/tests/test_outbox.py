import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import MetaData, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from tpsarena_common.events import CodeEmail, UserDeleted, decode
from tpsarena_common.outbox import Outbox, outbox_table

metadata = MetaData(schema="svc")
table = outbox_table(metadata)


@pytest.fixture(scope="module")
def database_url():
    with PostgresContainer("postgres:17", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="module")
def redis_url():
    with RedisContainer("redis:7") as container:
        yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"


@pytest.fixture
async def sessions(database_url):
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS svc"))
        await conn.run_sync(metadata.create_all)
        await conn.execute(text("TRUNCATE svc.outbox"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def redis(redis_url):
    client = Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


async def pending(sessions) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(table))


async def test_events_are_published_once_committed(sessions, redis):
    outbox = Outbox(table, sessions, redis)
    deleted = UserDeleted(user_id=uuid.uuid4())
    email = CodeEmail(kind="verify", email="a@b.c", username="a", code="123456")
    async with sessions() as session:
        await outbox.add(session, deleted, email)
        await session.commit()

    assert await outbox.flush() == 2
    assert [decode(fields) for _, fields in await redis.xrange("user.deleted")] == [deleted]
    assert [decode(fields) for _, fields in await redis.xrange("auth.emails")] == [email]
    assert await pending(sessions) == 0


async def test_rolled_back_events_are_never_published(sessions, redis):
    outbox = Outbox(table, sessions, redis)
    async with sessions() as session:
        await outbox.add(session, UserDeleted(user_id=uuid.uuid4()))
        await session.rollback()
    assert await outbox.flush() == 0
    assert await redis.xlen("user.deleted") == 0


async def test_events_wait_in_the_table_while_redis_is_down(sessions, redis):
    down = Redis.from_url("redis://127.0.0.1:1/0", decode_responses=True)
    e = UserDeleted(user_id=uuid.uuid4())
    async with sessions() as session:
        await Outbox(table, sessions, down).add(session, e)
        await session.commit()

    await Outbox(table, sessions, down).try_flush()  # logged, doesn't raise
    assert await pending(sessions) == 1

    assert await Outbox(table, sessions, redis).flush() == 1  # Redis is back (the worker's loop)
    assert [decode(fields) for _, fields in await redis.xrange("user.deleted")] == [e]
    await down.aclose()


async def test_flushes_in_batches_oldest_first(sessions, redis):
    outbox = Outbox(table, sessions, redis, batch_size=2)
    events = [UserDeleted(user_id=uuid.uuid4()) for _ in range(5)]
    async with sessions() as session:
        await outbox.add(session, *events)
        await session.commit()
    assert await outbox.flush() == 5
    assert [decode(fields) for _, fields in await redis.xrange("user.deleted")] == events
