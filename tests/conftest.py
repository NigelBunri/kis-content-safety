import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app

TEST_TOKEN = "test-internal-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_token", TEST_TOKEN)
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Internal-Auth": TEST_TOKEN})
        yield test_client
