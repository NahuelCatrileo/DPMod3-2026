import os
import tempfile

# Debe ir ANTES de importar app.*, porque config.py se cachea al importarse.
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
from fastapi.testclient import TestClient  # noqa: E402

import app.infra.models  # noqa: F401,E402  (registra la tabla en el metadata)
from app.infra.db import Base, SessionLocal, engine  # noqa: E402
from app.infra.events.publisher import (  # noqa: E402
    LogEventPublisher,
    set_event_publisher,
)
from app.infra.publishers.factory import set_publisher  # noqa: E402
from app.infra.publishers.mock import MockPublisher  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def events() -> LogEventPublisher:
    doble = LogEventPublisher()
    set_event_publisher(doble)
    yield doble
    set_event_publisher(None)


@pytest.fixture
def publisher() -> MockPublisher:
    p = MockPublisher(latency_seconds=0, failure_rate=0)
    set_publisher(p)
    yield p
    set_publisher(None)


@pytest.fixture
def session():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client(events, publisher):
    from app.main import app

    with TestClient(app) as c:
        yield c
