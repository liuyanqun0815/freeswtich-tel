# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY packages/common packages/common
COPY apps/api apps/api
COPY apps/config apps/config
COPY apps/engine apps/engine
COPY apps/ai_gw apps/ai_gw
COPY apps/push apps/push
COPY alembic.ini alembic.ini
COPY alembic alembic
COPY pyproject.toml pyproject.toml

RUN pip install --upgrade pip \
    && pip install "psycopg2-binary>=2.9" "uvicorn[standard]>=0.32" \
    && pip install -e packages/common \
    && pip install -e apps/api \
    && pip install -e apps/config \
    && pip install -e apps/engine \
    && pip install -e apps/ai_gw \
    && pip install -e apps/push

COPY deploy/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["api"]
