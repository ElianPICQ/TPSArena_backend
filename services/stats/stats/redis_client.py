from redis.asyncio import Redis
from tpsarena_common.events import Event, publish

from stats.config import get_settings

redis = Redis.from_url(get_settings().redis_url, decode_responses=True)


async def publish_event(event: Event) -> None:
    await publish(redis, event, maxlen=get_settings().events_stream_maxlen)
