import uuid

from tpsarena_common.events import CodeEmail, UserDeleted, publish

from auth.config import get_settings
from auth.redis_client import redis


async def publish_email(kind: str, email: str, username: str, code: str) -> None:
    event = CodeEmail(kind=kind, email=email, username=username, code=code)
    await publish(redis, event, maxlen=get_settings().emails_stream_maxlen)


async def publish_user_deleted(user_id: uuid.UUID) -> None:
    await publish(redis, UserDeleted(user_id=user_id), maxlen=get_settings().user_deleted_stream_maxlen)
