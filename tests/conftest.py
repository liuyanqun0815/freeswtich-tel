import pytest

from support import get_test_session, init_sqlite, seed_user
from vr_api.auth import get_db
from vr_api.main import app


@pytest.fixture(autouse=True)
def _auth_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    init_sqlite()
    seed_user("agent1", "secret-pass", "agent", must_change_password=False)
    app.dependency_overrides[get_db] = get_test_session
    yield
    app.dependency_overrides.clear()
