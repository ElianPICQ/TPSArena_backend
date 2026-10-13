"""Parties and invites, stored in Redis:

    social:party:{party_id}            hash    leader_id, status (lobby | launching | in_match), version, created_at, server_id
    social:party:{party_id}:members    set     up to 8 user ids
    social:user:{user_id}:party        string  party_id (one party per player)
    social:invite:{invite_id}          hash    party_id, from, to   (TTL 60 s)

Every change runs as one Lua script that bumps `version` and adds the party.updated snapshot
(or party.disbanded) to social.events itself. Consumers ignore snapshots that arrive out of
order thanks to `version`.
"""

import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tpsarena_common.events import Event, PartyInviteSent, PartyUpdated, encode

from social.config import get_settings
from social.redis_client import redis

EVENTS_STREAM = PartyUpdated.STREAM


def party_key(party_id: str) -> str:
    return f"social:party:{party_id}"


def members_key(party_id: str) -> str:
    return f"social:party:{party_id}:members"


def user_party_key(user_id: uuid.UUID | str) -> str:
    return f"social:user:{user_id}:party"


def invite_key(invite_id: str) -> str:
    return f"social:invite:{invite_id}"


def new_party_id() -> str:
    return str(uuid.uuid4())


def new_invite_id() -> str:
    return secrets.token_urlsafe(16)


# Each script adds its own event to social.events, so a party change and its event can't be
# separated (party state lives in Redis, not PostgreSQL, so this replaces the outbox).
# Every script gets the stream as its last KEY and (event_id, maxlen) as its last two ARGV.
# The `data` built here must match PartyUpdated / PartyDisbanded in tpsarena_common.events:
# tests decode every event with those models.
PUBLISH = """
local function publish_party(party_key, members_key, party_id)
  local stream, event_id, maxlen = KEYS[#KEYS], ARGV[#ARGV - 1], ARGV[#ARGV]
  local p = redis.call('HMGET', party_key, 'leader_id', 'status', 'version')
  local type_, data
  if not p[1] then
    type_ = 'party.disbanded'
    data = cjson.encode({party_id = party_id})
  else
    local members = redis.call('SMEMBERS', members_key)
    table.sort(members)
    type_ = 'party.updated'
    data = cjson.encode({party_id = party_id, version = tonumber(p[3]), leader_id = p[1], status = p[2],
                         member_ids = members})
  end
  redis.call('XADD', stream, 'MAXLEN', '~', maxlen, '*', 'event_id', event_id, 'type', type_, 'data', data)
end
"""

CREATE = redis.register_script(PUBLISH + """
-- KEYS: user party, party, members, stream   ARGV: user_id, party_id, now, event_id, maxlen
if redis.call('EXISTS', KEYS[1]) == 1 then return 'already_in_party' end
redis.call('HSET', KEYS[2], 'leader_id', ARGV[1], 'status', 'lobby', 'version', 1, 'created_at', ARGV[3])
redis.call('SADD', KEYS[3], ARGV[1])
redis.call('SET', KEYS[1], ARGV[2])
publish_party(KEYS[2], KEYS[3], ARGV[2])
return 'ok'
""")

JOIN = redis.register_script(PUBLISH + """
-- KEYS: invite, party, members, user party, stream   ARGV: user_id, party_id, max_size, event_id, maxlen
local invite = redis.call('HMGET', KEYS[1], 'party_id', 'to')
if not invite[1] or invite[1] ~= ARGV[2] or invite[2] ~= ARGV[1] then return 'invalid_invite' end
local status = redis.call('HGET', KEYS[2], 'status')
if not status then
  redis.call('DEL', KEYS[1])
  return 'party_gone'
end
if redis.call('EXISTS', KEYS[4]) == 1 then return 'already_in_party' end
if status ~= 'lobby' then return 'not_in_lobby' end
if redis.call('SCARD', KEYS[3]) >= tonumber(ARGV[3]) then return 'party_full' end
redis.call('SADD', KEYS[3], ARGV[1])
redis.call('SET', KEYS[4], ARGV[2])
redis.call('HINCRBY', KEYS[2], 'version', 1)
redis.call('DEL', KEYS[1])
publish_party(KEYS[2], KEYS[3], ARGV[2])
return 'ok'
""")

REMOVE_MEMBER = redis.register_script(PUBLISH + """
-- Leave, kick, disconnect, account deleted.
-- KEYS: party, members, user party (of the member), stream
-- ARGV: user_id, party_id, required leader ('' = any), event_id, maxlen
local leader = redis.call('HGET', KEYS[1], 'leader_id')
if not leader then return 'not_found' end
if ARGV[3] ~= '' and leader ~= ARGV[3] then return 'not_leader' end
if redis.call('SREM', KEYS[2], ARGV[1]) == 0 then return 'not_member' end
if redis.call('GET', KEYS[3]) == ARGV[2] then redis.call('DEL', KEYS[3]) end
if redis.call('SCARD', KEYS[2]) == 0 then
  redis.call('DEL', KEYS[1], KEYS[2])
  publish_party(KEYS[1], KEYS[2], ARGV[2])
  return 'disbanded'
end
if leader == ARGV[1] then
  redis.call('HSET', KEYS[1], 'leader_id', redis.call('SRANDMEMBER', KEYS[2]))
end
redis.call('HINCRBY', KEYS[1], 'version', 1)
publish_party(KEYS[1], KEYS[2], ARGV[2])
return 'ok'
""")

SET_STATUS = redis.register_script(PUBLISH + """
-- KEYS: party, members, stream
-- ARGV: party_id, from status, to status, required leader ('' = any), server_id to set ('' = clear),
--       required current server_id ('' = any), extra event (type, data, event_id; '' = none),
--       event_id, maxlen
-- The extra event (party.match_ready) is added after party.updated, only if the status changed.
local s = redis.call('HMGET', KEYS[1], 'status', 'leader_id', 'server_id')
if not s[1] then return 'not_found' end
if ARGV[4] ~= '' and s[2] ~= ARGV[4] then return 'not_leader' end
if s[1] ~= ARGV[2] then return 'wrong_status' end
if ARGV[6] ~= '' and s[3] ~= ARGV[6] then return 'wrong_server' end
redis.call('HSET', KEYS[1], 'status', ARGV[3])
if ARGV[5] ~= '' then
  redis.call('HSET', KEYS[1], 'server_id', ARGV[5])
else
  redis.call('HDEL', KEYS[1], 'server_id')
end
redis.call('HINCRBY', KEYS[1], 'version', 1)
publish_party(KEYS[1], KEYS[2], ARGV[1])
if ARGV[7] ~= '' then
  redis.call('XADD', KEYS[3], 'MAXLEN', '~', ARGV[#ARGV], '*', 'event_id', ARGV[9], 'type', ARGV[7], 'data', ARGV[8])
end
return 'ok'
""")


def event_args() -> list:
    """The last two ARGV of every script: the event id and the stream's MAXLEN."""
    return [str(uuid.uuid4()), get_settings().events_stream_maxlen]


@dataclass
class Party:
    party_id: str
    leader_id: uuid.UUID
    status: str
    version: int
    member_ids: list[uuid.UUID]
    server_id: str | None


async def get_party(party_id: str) -> Party | None:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hgetall(party_key(party_id))
        pipe.smembers(members_key(party_id))
        data, members = await pipe.execute()
    if not data:
        return None
    return Party(
        party_id=party_id,
        leader_id=uuid.UUID(data["leader_id"]),
        status=data["status"],
        version=int(data["version"]),
        member_ids=sorted(uuid.UUID(m) for m in members),
        server_id=data.get("server_id"),
    )


async def current_party_id(user_id: uuid.UUID) -> str | None:
    return await redis.get(user_party_key(user_id))


async def create(user_id: uuid.UUID) -> str:
    """Returns the new party id, or raises ValueError('already_in_party')."""
    party_id = new_party_id()
    result = await CREATE(
        keys=[user_party_key(user_id), party_key(party_id), members_key(party_id), EVENTS_STREAM],
        args=[str(user_id), party_id, int(time.time()), *event_args()],
    )
    if result != "ok":
        raise ValueError(result)
    return party_id


async def remove_member(party_id: str, user_id: uuid.UUID, required_leader: uuid.UUID | None = None) -> str:
    """Returns 'ok', 'disbanded', 'not_found', 'not_leader' or 'not_member'; publishes the change."""
    return await REMOVE_MEMBER(
        keys=[party_key(party_id), members_key(party_id), user_party_key(user_id), EVENTS_STREAM],
        args=[str(user_id), party_id, str(required_leader or ""), *event_args()],
    )


async def leave_current_party(user_id: uuid.UUID) -> None:
    """Disconnect, account deletion, or accepting another party's invite."""
    party_id = await current_party_id(user_id)
    if party_id:
        await remove_member(party_id, user_id)


async def set_status(
    party_id: str,
    from_status: str,
    to_status: str,
    required_leader: uuid.UUID | None = None,
    server_id: str | None = None,
    required_server_id: str | None = None,
    then: Event | None = None,
) -> str:
    """Change the status; publishes party.updated, then `then` (e.g. party.match_ready) if it changed."""
    extra = ["", "", ""]
    if then is not None:
        fields = encode(then)
        extra = [fields["type"], fields["data"], fields["event_id"]]
    return await SET_STATUS(
        keys=[party_key(party_id), members_key(party_id), EVENTS_STREAM],
        args=[
            party_id,
            from_status,
            to_status,
            str(required_leader or ""),
            server_id or "",
            required_server_id or "",
            *extra,
            *event_args(),
        ],
    )


async def create_invite(
    party_id: str, from_user_id: uuid.UUID, to_user_id: uuid.UUID, ttl_seconds: int
) -> tuple[str, datetime]:
    """Stores the invite and publishes party.invite_sent in one transaction. Returns its id and expiry."""
    invite_id = new_invite_id()
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    event = PartyInviteSent(
        invite_id=invite_id, party_id=party_id, from_user_id=from_user_id, to_user_id=to_user_id, expires_at=expires_at
    )
    key = invite_key(invite_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(key, mapping={"party_id": party_id, "from": str(from_user_id), "to": str(to_user_id)})
        pipe.expire(key, ttl_seconds)
        pipe.xadd(EVENTS_STREAM, encode(event), maxlen=get_settings().events_stream_maxlen, approximate=True)
        await pipe.execute()
    return invite_id, expires_at


async def get_invite(invite_id: str) -> dict[str, str] | None:
    return await redis.hgetall(invite_key(invite_id)) or None


async def join(invite_id: str, party_id: str, user_id: uuid.UUID, max_size: int) -> str:
    return await JOIN(
        keys=[invite_key(invite_id), party_key(party_id), members_key(party_id), user_party_key(user_id), EVENTS_STREAM],
        args=[str(user_id), party_id, max_size, *event_args()],
    )
