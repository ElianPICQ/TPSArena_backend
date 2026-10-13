"""Stats worker: records match results. Run with `python -m stats.worker`.

- match.finished   store the match, update totals, XP, levels, achievements and leaderboards,
                   then publish level ups and achievements on stats.progress (through the outbox)
- user.deleted     delete the player's totals and achievements, anonymize their match rows

It also publishes progress events left in the outbox (Redis was down when they were recorded).
"""

import asyncio
import logging

from tpsarena_common.events import Event, MatchFinished, UserDeleted
from tpsarena_common.streams import StreamConsumer, consumer_redis

from stats import recording
from stats.config import get_settings
from stats.db import SessionLocal
from stats.events import outbox

log = logging.getLogger("stats.worker")

STREAMS = [MatchFinished.STREAM, UserDeleted.STREAM]


async def handle(event: Event) -> None:
    match event:
        case MatchFinished():
            async with SessionLocal() as session:
                await recording.record_match(session, event)
            await outbox.try_flush()
            log.info("Recorded match %s (%d players)", event.match_id, len(event.players))

        case UserDeleted(user_id=user_id):
            async with SessionLocal() as session:
                await recording.delete_player(session, user_id)
            log.info("Deleted stats of %s", user_id)


async def run() -> None:
    consumer = StreamConsumer(consumer_redis(get_settings().redis_url), "stats", "stats", STREAMS, handle)
    await asyncio.gather(consumer.run(), outbox.run())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
