from redis.asyncio import Redis

from serverlist.config import get_settings

redis = Redis.from_url(get_settings().redis_url, decode_responses=True)


def server_key(server_id: str) -> str:
    return f"server:{server_id}"


def server_tickets_key(server_id: str) -> str:
    """Zset of the server's unused tickets, scored by expiry (ms): the slots they hold."""
    return f"server:{server_id}:tickets"


def ticket_key(ticket_id: str) -> str:
    return f"ticket:{ticket_id}"


ACTIVE_SERVERS = "servers:active"
