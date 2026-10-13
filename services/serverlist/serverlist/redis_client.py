from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError

from serverlist.config import get_settings

# Reconnect and retry when a pooled connection died (e.g. Redis restarted), instead of failing the request.
redis = Redis.from_url(
    get_settings().redis_url,
    decode_responses=True,
    retry=Retry(ExponentialBackoff(cap=1, base=0.05), retries=3),
    retry_on_error=[ConnectionError, TimeoutError],
    health_check_interval=30,
)


def server_key(server_id: str) -> str:
    return f"serverlist:server:{server_id}"


def server_tickets_key(server_id: str) -> str:
    """Zset of the server's unused tickets, scored by expiry (ms): the slots they hold."""
    return f"serverlist:server:{server_id}:tickets"


def server_party_key(server_id: str) -> str:
    """The party a server hosts. Unlike the server hash (15 s TTL), it lasts until the server is
    removed (1 day at most), so server.removed names the party even if the sweep comes late."""
    return f"serverlist:server:{server_id}:party"


def ticket_key(ticket_id: str) -> str:
    return f"serverlist:ticket:{ticket_id}"


ACTIVE_SERVERS = "serverlist:servers:active"
EVENTS_STREAM = "serverlist.events"
