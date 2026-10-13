"""Events about rows in PostgreSQL go through the outbox: written in the same transaction as the
change, then published (right after the commit, or by the worker if Redis was down)."""

from tpsarena_common.outbox import Outbox

from stats.config import get_settings
from stats.db import SessionLocal
from stats.models import Outbox as OutboxTable
from stats.redis_client import redis

outbox = Outbox(OutboxTable, SessionLocal, redis, default_maxlen=get_settings().events_stream_maxlen)
