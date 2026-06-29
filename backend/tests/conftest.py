import os

import pytest

from app.api.audits.service import repository
from app.main import app

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-32-chars")


@pytest.fixture(autouse=True)
def reset_app_state():
    repository.clear()
    app.dependency_overrides.clear()
    yield
    repository.clear()
    app.dependency_overrides.clear()
