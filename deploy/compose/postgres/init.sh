#!/bin/sh
# Creates the Z and LiteLLM databases and the application role (append-only audit: see migration 0001).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE z_app LOGIN PASSWORD '${Z_APP_DB_PASSWORD}';
CREATE DATABASE z OWNER "$POSTGRES_USER";
CREATE DATABASE litellm OWNER "$POSTGRES_USER";
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname z -c "CREATE EXTENSION IF NOT EXISTS vector;"
