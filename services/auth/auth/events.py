"""Auth's events go through its outbox: written in the same transaction as the change, then published."""

from tpsarena_common.events import CodeEmail, SessionsRevoked, UserDeleted
from tpsarena_common.outbox import Outbox

from auth.config import get_settings
from auth.db import SessionLocal
from auth.models import Outbox as OutboxTable
from auth.redis_client import redis

settings = get_settings()
outbox = Outbox(
    OutboxTable,
    SessionLocal,
    redis,
    maxlen={
        CodeEmail.STREAM: settings.emails_stream_maxlen,
        UserDeleted.STREAM: settings.user_deleted_stream_maxlen,
        SessionsRevoked.STREAM: settings.sessions_stream_maxlen,
    },
)
