import pytest

from app import env


@pytest.fixture(autouse=True)
def _ignore_the_developers_env_file(monkeypatch):
    """Config readers load api/.env on first use. A test must never see the
    developer's own file, or a configured provider on one laptop fails a test
    that passes in CI. Tests about the file point it at a tmp path themselves."""
    monkeypatch.setattr(env, "_loaded", True)
