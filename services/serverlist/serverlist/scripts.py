"""Lua scripts: each one checks and changes a server atomically, so concurrent requests
can't hand out the same slot twice or undo each other's status change.

A slot is taken either by a connected player (`players`, from heartbeats and verified
tickets) or by an unused ticket (the server's tickets zset, entries expire with the ticket).

Events (serverlist.events) are added by the scripts themselves, so a change and its event can't
be separated. Their `data` must match the models in tpsarena_common.events (tests decode them).
"""

from serverlist.redis_client import redis

# KEYS: server hash, server tickets zset, active zset, events stream, server party
# ARGV: now_ms, ttl_s, reported_status, players, map, server_id, event_id, stream maxlen
# Returns the server hash after the update, or nil if the server isn't registered (expired).
HEARTBEAT = redis.register_script("""
if redis.call('EXISTS', KEYS[1]) == 0 then return nil end
local now = tonumber(ARGV[1])
local current = redis.call('HGET', KEYS[1], 'status')
local new = ARGV[3]

-- The server learns it was reserved from this heartbeat's answer, so an "idle" sent before
-- then must not cancel the reservation. After the reservation window it may go back to idle,
-- and the party is told it won't play there (server.reservation_expired).
if current == 'reserved' and new == 'idle' then
  local s = redis.call('HMGET', KEYS[1], 'reserved_until', 'party_id')
  if now < tonumber(s[1]) then
    new = 'reserved'
  elseif s[2] then
    local data = cjson.encode({server_id = ARGV[6], party_id = s[2]})
    redis.call('XADD', KEYS[4], 'MAXLEN', '~', ARGV[8], '*',
      'event_id', ARGV[7], 'type', 'server.reservation_expired', 'data', data)
  end
end

if new == 'idle' then
  redis.call('HDEL', KEYS[1], 'party_id', 'reserved_until', 'started_at')
  redis.call('DEL', KEYS[2], KEYS[5])
elseif new == 'in_progress' and current ~= 'in_progress' then
  redis.call('HSET', KEYS[1], 'started_at', now)
end

redis.call('HSET', KEYS[1], 'status', new, 'players', ARGV[4], 'map', ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('ZADD', KEYS[3], now, ARGV[6])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local result = redis.call('HGETALL', KEYS[1])
table.insert(result, 'reserved_slots')
table.insert(result, redis.call('ZCARD', KEYS[2]))
return result
""")

# KEYS: server hash, server tickets zset, ticket key
# ARGV: now_ms, ticket_ttl_ms, ticket_id, ticket_json
# Returns 'ok', 'not_found', 'not_joinable' or 'full'.
JOIN_TICKET = redis.register_script("""
if redis.call('EXISTS', KEYS[1]) == 0 then return 'not_found' end
local s = redis.call('HMGET', KEYS[1], 'status', 'players', 'max_players')
if s[1] ~= 'in_progress' then return 'not_joinable' end
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if tonumber(s[2]) + redis.call('ZCARD', KEYS[2]) >= tonumber(s[3]) then return 'full' end
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[2], ARGV[2])
redis.call('SET', KEYS[3], ARGV[4], 'PX', ARGV[2])
return 'ok'
""")

# KEYS: server hash, server tickets zset, active zset, events stream, server party
# ARGV: server_id, reason (timed_out | deregistered), stale_before_ms ('' = remove now), event_id, stream maxlen
# Removes the server and publishes server.removed (with the party it hosted, if any).
# Returns 1, or 0 if it was already removed or (timed_out) sent a heartbeat meanwhile.
REMOVE = redis.register_script("""
if ARGV[3] ~= '' then
  local score = redis.call('ZSCORE', KEYS[3], ARGV[1])
  if not score or tonumber(score) > tonumber(ARGV[3]) then return 0 end
end
local listed = redis.call('ZREM', KEYS[3], ARGV[1])
if listed == 0 and redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local data = {server_id = ARGV[1], reason = ARGV[2]}
-- The party key outlives the hash: a server swept late (Server List was down) still names its party.
data['party_id'] = redis.call('HGET', KEYS[1], 'party_id') or redis.call('GET', KEYS[5]) or nil
redis.call('DEL', KEYS[1], KEYS[2], KEYS[5])
redis.call('XADD', KEYS[4], 'MAXLEN', '~', ARGV[5], '*', 'event_id', ARGV[4], 'type', 'server.removed', 'data', cjson.encode(data))
return 1
""")

# KEYS: server hash, server tickets zset, one ticket key per member, then the server party key
# ARGV: now_ms, ticket_ttl_ms, party_id, reserved_until_ms, build_version, then (ticket_id, ticket_json) per member
# Returns 1 if the server was reserved, 0 if it wasn't available any more.
ALLOCATE = redis.register_script("""
local s = redis.call('HMGET', KEYS[1], 'status', 'build_version', 'max_players')
if s[1] ~= 'idle' or s[2] ~= ARGV[5] then return 0 end
local members = #KEYS - 3
if members > tonumber(s[3]) then return 0 end
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
redis.call('HSET', KEYS[1], 'status', 'reserved', 'party_id', ARGV[3], 'reserved_until', ARGV[4])
redis.call('DEL', KEYS[2])
for i = 1, members do
  redis.call('ZADD', KEYS[2], now + ttl, ARGV[4 + 2 * i])
  redis.call('SET', KEYS[2 + i], ARGV[5 + 2 * i], 'PX', ttl)
end
redis.call('PEXPIRE', KEYS[2], ttl)
redis.call('SET', KEYS[#KEYS], ARGV[3], 'EX', 86400)
return 1
""")

# KEYS: ticket key, server hash, server tickets zset
# ARGV: ticket_id, server_id
# Returns the ticket JSON, or nil. The ticket is deleted either way (single use, like GETDEL).
VERIFY_TICKET = redis.register_script("""
local value = redis.call('GETDEL', KEYS[1])
if not value then return nil end
if cjson.decode(value)['server_id'] ~= ARGV[2] then return nil end
redis.call('ZREM', KEYS[3], ARGV[1])
-- Count the player right away; the next heartbeat brings the real number.
if redis.call('EXISTS', KEYS[2]) == 1 then redis.call('HINCRBY', KEYS[2], 'players', 1) end
return value
""")
