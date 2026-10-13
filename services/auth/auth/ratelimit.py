from auth.errors import APIError
from auth.redis_client import redis


async def hit(action: str, key: str, limit: int, window_seconds: int) -> None:
    """Count one attempt for `key`; raise 429 once more than `limit` happen in the window."""
    redis_key = f"auth:ratelimit:{action}:{key}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    if count > limit:
        raise APIError(429, "rate_limited", "Too many attempts, try again later.")
