#!/bin/sh
# Shared Redis for the full stack: streams between services + each service's own keys.
# One ACL user per service, limited to its key prefix and the streams it uses, like each
# PostgreSQL user can only reach its own database. Passwords come from the environment.
set -e

for var in REDIS_ADMIN_PASSWORD AUTH_REDIS_PASSWORD SERVERLIST_REDIS_PASSWORD SOCIAL_REDIS_PASSWORD STATS_REDIS_PASSWORD; do
    eval "value=\${$var:-}"
    if [ -z "$value" ]; then
        echo "Missing $var" >&2
        exit 1
    fi
done

ACL_FILE=/tmp/users.acl
# ~pattern: read/write keys  %W~pattern: write only (publish to a stream)  -@dangerous: no FLUSHDB, KEYS, CONFIG...
# Consumers need write access to the streams they read: consumer groups record acknowledgements.
cat > "$ACL_FILE" <<ACL
user default off
user admin on >$REDIS_ADMIN_PASSWORD ~* &* +@all
user auth on >$AUTH_REDIS_PASSWORD resetchannels ~auth:* ~auth.emails %W~user.deleted +@all -@dangerous
user serverlist on >$SERVERLIST_REDIS_PASSWORD resetchannels ~serverlist:* +@all -@dangerous
user social on >$SOCIAL_REDIS_PASSWORD resetchannels ~social:* ~user.deleted ~presence.changed ~match.finished %W~social.events +@all -@dangerous
user stats on >$STATS_REDIS_PASSWORD resetchannels ~stats:* ~match.finished ~user.deleted %W~stats.progress +@all -@dangerous
ACL
chmod 600 "$ACL_FILE"

exec redis-server --appendonly yes --aclfile "$ACL_FILE"
