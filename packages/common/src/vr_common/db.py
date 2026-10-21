"""Database helpers and tenant defaults."""

import os

DEFAULT_TENANT = os.environ.get("DEFAULT_TENANT", "t001")


def get_database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://vr:vr@127.0.0.1:5432/vr",
    )
