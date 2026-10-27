#!/bin/sh
set -e

# Normalize asyncpg URLs for SQLAlchemy sync engines / Alembic.
if [ -n "${DATABASE_URL:-}" ]; then
  export DATABASE_URL="$(printf '%s' "$DATABASE_URL" | sed 's|postgresql+asyncpg://|postgresql+psycopg2://|')"
fi

role="${1:-api}"
shift || true

case "$role" in
  migrate)
    exec alembic upgrade head
    ;;
  api)
    exec uvicorn vr_api.main:app --host 0.0.0.0 --port 8080 "$@"
    ;;
  config)
    exec uvicorn vr_config.main:app --host 0.0.0.0 --port 8081 "$@"
    ;;
  push)
    exec uvicorn vr_push.main:app --host 0.0.0.0 --port 8082 "$@"
    ;;
  engine)
    exec python -m vr_engine.reader "$@"
    ;;
  ai_gw)
    exec uvicorn vr_ai.server:app --host 0.0.0.0 --port 8040 "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
