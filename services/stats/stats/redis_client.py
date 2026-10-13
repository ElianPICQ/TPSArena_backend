from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError
from tpsarena_common.events import Event, publish

from stats.config import get_settings

# Reconnect and retry when a pooled connection died (e.g. Redis restarted), instead of failing the request.
redis = Redis.from_url(
    get_settings().redis_url,
    decode_responses=True,
    retry=Retry(ExponentialBackoff(cap=1, base=0.05), retries=3),
    retry_on_error=[ConnectionError, TimeoutError],
    health_check_interval=30,
)


async def publish_event(event: Event) -> None:
    await publish(redis, event, maxlen=get_settings().events_stream_maxlen)
