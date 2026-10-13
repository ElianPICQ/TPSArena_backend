"""Lua scripts: each one checks and changes a server atomically, so concurrent requests
can't hand out the same slot twice or undo each other's status change.

A slot is taken either by a connected player (`players`, from heartbeats and verified
tickets) or by an unused ticket (the server's tickets zset, entries expire with the ticket).
"""

from serverlist.redis_client import redis

# KEYS: server hash, server tickets zset, active zset
# ARGV: now_ms, ttl_s, reported_status, players, map, server_id
# Returns the server hash after the update, or nil if the server isn't registered (expired).
HEARTBEAT = redis.register_script("""
if redis.call('EXISTS', KEYS[1]) == 0 then return nil end
local now = tonumber(ARGV[1])
local current = redis.call('HGET', KEYS[1], 'status')
local new = ARGV[3]

-- The server learns it was reserved from this heartbeat's answer, so an "idle" sent before
-- then must not cancel the reservation. After the reservation window it may go back to idle.
if current == 'reserved' and new == 'idle' then
  if now < tonumber(redis.call('HGET', KEYS[1], 'reserved_until')) then new = 'reserved' end
end

if new == 'idle' then
  redis.call('HDEL', KEYS[1], 'party_id', 'reserved_until', 'started_at')
  redis.call('DEL', KEYS[2])
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

# KEYS: server hash, server tickets zset, then one ticket key per member
# ARGV: now_ms, ticket_ttl_ms, party_id, reserved_until_ms, build_version, then (ticket_id, ticket_json) per member
# Returns 1 if the server was reserved, 0 if it wasn't available any more.
ALLOCATE = redis.register_script("""
local s = redis.call('HMGET', KEYS[1], 'status', 'build_version', 'max_players')
if s[1] ~= 'idle' or s[2] ~= ARGV[5] then return 0 end
local members = #KEYS - 2
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
