"""Parties and invites, stored in Redis:

    party:{party_id}            hash    leader_id, status (lobby | launching | in_match), version, created_at, server_id
    party:{party_id}:members    set     up to 8 user ids
    user:{user_id}:party        string  party_id (one party per player)
    invite:{invite_id}          hash    party_id, from, to   (TTL 60 s)

Every change runs as one Lua script and bumps `version`, which party.updated carries so
consumers can ignore snapshots that arrive out of order.
"""

import secrets
import time
import uuid
from dataclasses import dataclass

from tpsarena_common.events import PartyDisbanded, PartyUpdated

from social.redis_client import publish_event, redis


def party_key(party_id: str) -> str:
    return f"party:{party_id}"


def members_key(party_id: str) -> str:
    return f"party:{party_id}:members"


def user_party_key(user_id: uuid.UUID | str) -> str:
    return f"user:{user_id}:party"


def invite_key(invite_id: str) -> str:
    return f"invite:{invite_id}"


def new_party_id() -> str:
    return str(uuid.uuid4())


def new_invite_id() -> str:
    return secrets.token_urlsafe(16)


CREATE = redis.register_script("""
-- KEYS: user party, party, members   ARGV: user_id, party_id, now
if redis.call('EXISTS', KEYS[1]) == 1 then return 'already_in_party' end
redis.call('HSET', KEYS[2], 'leader_id', ARGV[1], 'status', 'lobby', 'version', 1, 'created_at', ARGV[3])
redis.call('SADD', KEYS[3], ARGV[1])
redis.call('SET', KEYS[1], ARGV[2])
return 'ok'
""")

JOIN = redis.register_script("""
-- KEYS: invite, party, members, user party   ARGV: user_id, party_id, max_size
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
return 'ok'
""")

REMOVE_MEMBER = redis.register_script("""
-- Leave, kick, disconnect, account deleted.
-- KEYS: party, members, user party (of the member)   ARGV: user_id, party_id, required leader ('' = any)
local leader = redis.call('HGET', KEYS[1], 'leader_id')
if not leader then return 'not_found' end
if ARGV[3] ~= '' and leader ~= ARGV[3] then return 'not_leader' end
if redis.call('SREM', KEYS[2], ARGV[1]) == 0 then return 'not_member' end
if redis.call('GET', KEYS[3]) == ARGV[2] then redis.call('DEL', KEYS[3]) end
if redis.call('SCARD', KEYS[2]) == 0 then
  redis.call('DEL', KEYS[1], KEYS[2])
  return 'disbanded'
end
if leader == ARGV[1] then
  redis.call('HSET', KEYS[1], 'leader_id', redis.call('SRANDMEMBER', KEYS[2]))
end
redis.call('HINCRBY', KEYS[1], 'version', 1)
return 'ok'
""")

SET_STATUS = redis.register_script("""
-- KEYS: party
-- ARGV: from status, to status, required leader ('' = any), server_id to set ('' = clear),
--       required current server_id ('' = any)
local s = redis.call('HMGET', KEYS[1], 'status', 'leader_id', 'server_id')
if not s[1] then return 'not_found' end
if ARGV[3] ~= '' and s[2] ~= ARGV[3] then return 'not_leader' end
if s[1] ~= ARGV[1] then return 'wrong_status' end
if ARGV[5] ~= '' and s[3] ~= ARGV[5] then return 'wrong_server' end
redis.call('HSET', KEYS[1], 'status', ARGV[2])
if ARGV[4] ~= '' then
  redis.call('HSET', KEYS[1], 'server_id', ARGV[4])
else
  redis.call('HDEL', KEYS[1], 'server_id')
end
redis.call('HINCRBY', KEYS[1], 'version', 1)
return 'ok'
""")


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


async def publish_party(party_id: str) -> Party | None:
    """Publish the party's current state: party.updated, or party.disbanded if it's gone."""
    party = await get_party(party_id)
    if party is None:
        await publish_event(PartyDisbanded(party_id=party_id))
    else:
        await publish_event(
            PartyUpdated(
                party_id=party.party_id,
                version=party.version,
                leader_id=party.leader_id,
                status=party.status,
                member_ids=party.member_ids,
            )
        )
    return party


async def create(user_id: uuid.UUID) -> str:
    """Returns the new party id, or raises ValueError('already_in_party')."""
    party_id = new_party_id()
    result = await CREATE(
        keys=[user_party_key(user_id), party_key(party_id), members_key(party_id)],
        args=[str(user_id), party_id, int(time.time())],
    )
    if result != "ok":
        raise ValueError(result)
    return party_id


async def remove_member(party_id: str, user_id: uuid.UUID, required_leader: uuid.UUID | None = None) -> str:
    """Returns 'ok', 'disbanded', 'not_found', 'not_leader' or 'not_member'; publishes the change."""
    result = await REMOVE_MEMBER(
        keys=[party_key(party_id), members_key(party_id), user_party_key(user_id)],
        args=[str(user_id), party_id, str(required_leader or "")],
    )
    if result in ("ok", "disbanded"):
        await publish_party(party_id)
    return result


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
) -> str:
    return await SET_STATUS(
        keys=[party_key(party_id)],
        args=[from_status, to_status, str(required_leader or ""), server_id or "", required_server_id or ""],
    )


async def create_invite(party_id: str, from_user_id: uuid.UUID, to_user_id: uuid.UUID, ttl_seconds: int) -> str:
    invite_id = new_invite_id()
    key = invite_key(invite_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(key, mapping={"party_id": party_id, "from": str(from_user_id), "to": str(to_user_id)})
        pipe.expire(key, ttl_seconds)
        await pipe.execute()
    return invite_id


async def get_invite(invite_id: str) -> dict[str, str] | None:
    return await redis.hgetall(invite_key(invite_id)) or None


async def join(invite_id: str, party_id: str, user_id: uuid.UUID, max_size: int) -> str:
    return await JOIN(
        keys=[invite_key(invite_id), party_key(party_id), members_key(party_id), user_party_key(user_id)],
        args=[str(user_id), party_id, max_size],
    )
