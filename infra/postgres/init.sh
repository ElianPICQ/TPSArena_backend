#!/bin/sh
# Runs once, when the postgres data volume is first created.
# One schema per service, each with its own user that can only access that schema.
# For an existing volume, run the matching block by hand (see `create_service_schema` below).
set -e

create_service_schema() {
    # $1 = schema, $2 = role, $3 = password
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -v schema="$1" -v role="$2" -v password="$3" <<-'EOSQL'
	CREATE ROLE :"role" LOGIN PASSWORD :'password';
	CREATE SCHEMA :"schema" AUTHORIZATION :"role";
	ALTER ROLE :"role" SET search_path = :"schema";
EOSQL
}

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "REVOKE ALL ON SCHEMA public FROM PUBLIC;"

create_service_schema auth auth_svc "$AUTH_DB_PASSWORD"
create_service_schema social social_svc "$SOCIAL_DB_PASSWORD"
create_service_schema stats stats_svc "$STATS_DB_PASSWORD"
