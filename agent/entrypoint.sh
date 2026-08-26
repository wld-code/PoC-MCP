#!/bin/sh
# Runs Alembic migrations before the real command, but only for the backend
# API service (RUN_MIGRATIONS=1, set in docker-compose.yml) — the plain CLI
# image (headless.py/agent.py) has no DB and shouldn't need Postgres reachable
# just to answer one question.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "[entrypoint] running alembic upgrade head..."
    alembic -c backend/alembic.ini upgrade head
fi

exec "$@"
