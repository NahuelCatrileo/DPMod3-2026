import os
import tempfile

_tmp_db = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_db}")
os.environ.setdefault("PUBLISHER_MODE", "mock")
os.environ.setdefault("EVENT_TRANSPORT", "log")
os.environ.setdefault("MOCK_LATENCY_SECONDS", "0")
os.environ.setdefault("MOCK_FAILURE_RATE", "0")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("SESSION_SECRET_KEY", "test-secret")

import pytest  # noqa: E402

from app.infra.publishers.factory import set_publisher  # noqa: E402
from app.infra.publishers.mock import MockPublisher  # noqa: E402


@pytest.fixture
def publisher() -> MockPublisher:
    p = MockPublisher(latency_seconds=0, failure_rate=0)
    set_publisher(p)
    yield p
    set_publisher(None)