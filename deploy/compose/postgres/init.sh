#!/bin/sh
# Creates the Z and LiteLLM databases and their roles, as the Helm chart does:
#   z       owner of the z database: migrations only (the audit trigger and grants, migration 0001)
#   z_app   application role (cannot rewrite the audit log)
#   litellm owns the litellm database and nothing else (LiteLLM is reachable by runners: never the Z owner)
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v app_pw="$Z_APP_DB_PASSWORD" -v litellm_pw="$LITELLM_DB_PASSWORD" <<'SQL'
CREATE ROLE z_app LOGIN PASSWORD :'app_pw';
CREATE ROLE litellm LOGIN PASSWORD :'litellm_pw';
CREATE DATABASE z OWNER z;
CREATE DATABASE litellm OWNER litellm;
REVOKE ALL ON DATABASE litellm FROM PUBLIC;
REVOKE ALL ON DATABASE z FROM PUBLIC;
GRANT CONNECT ON DATABASE z TO z_app;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname z -c "CREATE EXTENSION IF NOT EXISTS vector;"
