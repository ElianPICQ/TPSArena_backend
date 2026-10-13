"""Social worker: reacts to other services' events. Run with `python -m social.worker`.

- user.deleted       remove the player from their party, delete their profile, friends and requests
- presence.changed   offline players are removed from their party
- match.finished     the party that launched the match goes back to the lobby
- server.removed     same, when the party's server crashed or shut down before the match ended
- server.reservation_expired   same, when the party never started the match on its server

It also publishes friend events left in the outbox (Redis was down when the request tried).
"""

import asyncio
import logging

from sqlalchemy import delete
from tpsarena_common.events import (
    Event,
    MatchFinished,
    PresenceChanged,
    ReservationExpired,
    ServerRemoved,
    UserDeleted,
)
from tpsarena_common.streams import StreamConsumer, consumer_redis

from social import parties
from social.config import get_settings
from social.db import SessionLocal
from social.events import outbox
from social.models import Player

log = logging.getLogger("social.worker")

STREAMS = [UserDeleted.STREAM, PresenceChanged.STREAM, MatchFinished.STREAM, ServerRemoved.STREAM]


async def handle(event: Event) -> None:
    match event:
        case UserDeleted(user_id=user_id):
            await parties.leave_current_party(user_id)
            async with SessionLocal() as session:
                # Friendships and friend requests go too (ON DELETE CASCADE).
                await session.execute(delete(Player).where(Player.user_id == user_id))
                await session.commit()
            log.info("Deleted social data of %s", user_id)

        case PresenceChanged(user_id=user_id, status="offline"):
            await parties.leave_current_party(user_id)

        case (
            MatchFinished(party_id=str() as party_id, server_id=server_id)
            | ServerRemoved(party_id=str() as party_id, server_id=server_id)
            | ReservationExpired(party_id=party_id, server_id=server_id)
        ):
            # Only if the party is still on that server: it may have launched again since.
            await parties.set_status(party_id, "in_match", "lobby", required_server_id=str(server_id))


async def run() -> None:
    consumer = StreamConsumer(consumer_redis(get_settings().redis_url), "social", "social", STREAMS, handle)
    await asyncio.gather(consumer.run(), outbox.run())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
