"""Transactional outbox: publish events that can't be lost once the change they describe is committed.

Publishing to Redis after a PostgreSQL commit is two steps, and a failure between them loses the
event. Instead, the event is written to the service's `outbox` table in the same transaction as
the change, then published from there:

    async with SessionLocal() as session:
        ...change...
        await outbox.add(session, UserDeleted(user_id=user_id))
        await session.commit()
    await outbox.try_flush()        # right away, so consumers don't wait

    await outbox.run()              # in the worker: publishes whatever is left (Redis was down, crash)

Published rows are deleted. A crash between publishing and deleting publishes a row again, which
is fine: consumers skip event ids they already handled. Concurrent flushes skip each other's rows
(`FOR UPDATE SKIP LOCKED`), so events from different flushes may arrive out of order.

Needs the `outbox` extra (SQLAlchemy): `tpsarena-common[outbox]`.
"""

import asyncio
import logging
from collections.abc import Callable

from redis.asyncio import Redis
from sqlalchemy import BigInteger, Column, DateTime, Identity, MetaData, String, Table, Text, Uuid, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tpsarena_common.events import Event, encode

log = logging.getLogger(__name__)


def outbox_table(metadata: MetaData) -> Table:
    """The `outbox` table in the service's own schema (the metadata's), for its models and Alembic."""
    return Table(
        "outbox",
        metadata,
        Column("id", BigInteger, Identity(), primary_key=True),
        Column("stream", String(100), nullable=False),
        Column("event_id", Uuid, nullable=False),
        Column("type", String(100), nullable=False),
        Column("data", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


class Outbox:
    def __init__(
        self,
        table: Table,
        session_factory: Callable[[], AsyncSession],
        redis: Redis,
        maxlen: dict[str, int] | None = None,
        default_maxlen: int = 100_000,
        batch_size: int = 100,
    ):
        self.table = table
        self.session_factory = session_factory
        self.redis = redis
        self.maxlen = maxlen or {}
        self.default_maxlen = default_maxlen
        self.batch_size = batch_size

    async def add(self, session: AsyncSession, *events: Event) -> None:
        """Queue events in the caller's transaction (not committed here)."""
        if not events:
            return
        rows = []
        for e in events:
            fields = encode(e)
            rows.append({"stream": e.STREAM, "event_id": e.event_id, "type": fields["type"], "data": fields["data"]})
        await session.execute(self.table.insert(), rows)

    async def flush(self) -> int:
        """Publish pending events, oldest first, until none are left. Returns how many were published."""
        published = 0
        while True:
            count = await self._flush_batch()
            published += count
            if count < self.batch_size:
                return published

    async def _flush_batch(self) -> int:
        t = self.table
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(t).order_by(t.c.id).limit(self.batch_size).with_for_update(skip_locked=True)
                )
            ).all()
            if not rows:
                return 0
            async with self.redis.pipeline(transaction=False) as pipe:
                for row in rows:
                    pipe.xadd(
                        row.stream,
                        {"event_id": str(row.event_id), "type": row.type, "data": row.data},
                        maxlen=self.maxlen.get(row.stream, self.default_maxlen),
                        approximate=True,
                    )
                await pipe.execute()
            await session.execute(delete(t).where(t.c.id.in_([row.id for row in rows])))
            await session.commit()
            return len(rows)

    async def try_flush(self) -> None:
        """Flush after a commit, on the request path: a failure is logged, the worker publishes later."""
        try:
            await self.flush()
        except Exception as exc:
            log.warning("Outbox flush failed, the worker will publish later: %r", exc)

    async def run(self, interval_seconds: float = 2.0) -> None:
        """Publish leftovers forever (run in the service's worker)."""
        while True:
            try:
                count = await self.flush()
                if count:
                    log.info("Published %d event(s) from the outbox", count)
            except Exception as exc:
                log.warning("Outbox flush failed, retrying: %r", exc)
            await asyncio.sleep(interval_seconds)
