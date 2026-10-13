"""Events about rows in PostgreSQL go through the outbox: written in the same transaction as the
change, then published (right after the commit, or by the worker if Redis was down)."""

from tpsarena_common.outbox import Outbox

from social.config import get_settings
from social.db import SessionLocal
from social.models import Outbox as OutboxTable
from social.redis_client import redis

outbox = Outbox(OutboxTable, SessionLocal, redis, default_maxlen=get_settings().events_stream_maxlen)
