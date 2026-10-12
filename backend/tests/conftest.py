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
