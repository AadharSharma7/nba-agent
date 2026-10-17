import os

import pytest
from pydantic_ai import models

from nba_agent.config import Settings
from nba_agent.database import make_pool


@pytest.fixture(autouse=True)
def no_paid_model_requests(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture
async def pool():
    if os.environ.get("NBA_TEST_DATABASE") != "1":
        pytest.skip("Set NBA_TEST_DATABASE=1 to test the imported local database")
    async with make_pool(Settings()) as result:
        yield result


@pytest.fixture
def unmetered_http(monkeypatch):
    """Mock-model tests must not spend or be blocked by real visitor budgets."""
    from nba_agent import api, security

    async def no_budget(*args, **kwargs):
        pass

    monkeypatch.setattr(api, "check_chat_budget", no_budget)
    monkeypatch.setattr(security, "limited", no_budget)


@pytest.fixture
def owner_access():
    from uuid import uuid4

    from nba_agent.security import require_owner

    def authorize(app):
        async def owner():
            return uuid4()

        app.dependency_overrides[require_owner] = owner
        return app

    return authorize
