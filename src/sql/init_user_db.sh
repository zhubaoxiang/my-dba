#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE scancenter LOGIN PASSWORD 'Admin123#';
    GRANT ALL PRIVILEGES ON DATABASE scancenterdb TO scancenter;
EOSQL
