#!/bin/sh
# Shared Redis for the full stack: streams between services + each service's own keys.
# One ACL user per service, limited to its key prefix and the streams it uses, like each
# PostgreSQL user can only reach its own database. Passwords come from the environment.
set -e

for var in REDIS_ADMIN_PASSWORD AUTH_REDIS_PASSWORD SERVERLIST_REDIS_PASSWORD SOCIAL_REDIS_PASSWORD STATS_REDIS_PASSWORD REALTIME_REDIS_PASSWORD; do
    eval "value=\${$var:-}"
    if [ -z "$value" ]; then
        echo "Missing $var" >&2
        exit 1
    fi
done

ACL_FILE=/tmp/users.acl
# ~pattern: read/write keys  %W~pattern: write only (publish to a stream)  &pattern: Pub/Sub channels  -@dangerous: no FLUSHDB, KEYS, CONFIG...
# Consumers need write access to the streams they read: consumer groups record acknowledgements.
# Server List and Social add their events from Lua scripts, which need read/write on every key they're given.
#
# `default` is the admin (for redis-cli: `redis-cli -a <REDIS_ADMIN_PASSWORD>`). It must keep full
# rights: at startup Redis replays the append-only file as the default user, and a transaction
# it isn't allowed to run is dropped — with `default` switched off, every MULTI/EXEC and Lua
# script result (parties, tickets, leaderboards...) would be lost on each restart.
cat > "$ACL_FILE" <<ACL
user default on >$REDIS_ADMIN_PASSWORD ~* &* +@all
user auth on >$AUTH_REDIS_PASSWORD resetchannels ~auth:* ~auth.emails %W~user.deleted %W~auth.sessions +@all -@dangerous
user serverlist on >$SERVERLIST_REDIS_PASSWORD resetchannels ~serverlist:* ~serverlist.events +@all -@dangerous
user social on >$SOCIAL_REDIS_PASSWORD resetchannels ~social:* ~user.deleted ~presence.changed ~match.finished ~serverlist.events ~social.events +@all -@dangerous
user stats on >$STATS_REDIS_PASSWORD resetchannels ~stats:* ~match.finished ~user.deleted %W~stats.progress +@all -@dangerous
user realtime on >$REALTIME_REDIS_PASSWORD resetchannels &realtime:* ~realtime:* ~social.events ~stats.progress ~player.match ~user.deleted ~auth.sessions ~serverlist.events %W~presence.changed +@all -@dangerous
ACL
chmod 600 "$ACL_FILE"

exec redis-server --appendonly yes --aclfile "$ACL_FILE"
