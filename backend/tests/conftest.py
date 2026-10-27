import os

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://medication_doc_verifier:medication_doc_verifier@localhost:5432/medication_doc_verifier_test"
)
os.environ["DATABASE_URL"] = TEST_DB
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["JWT_SECRET"] = "test-secret-with-at-least-32-bytes-of-length"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from tests.helpers import make_reference  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, documents, document_blobs, document_pages, chunks, jobs, job_stages, "
                "summaries, critical_points, medication_entities, verifications, verification_citations CASCADE"
            )
        )


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def reference(db):
    return make_reference(db)


def signup(client: TestClient, email: str) -> TestClient:
    r = client.post("/api/auth/signup", json={"email": email, "password": "password-123"})
    assert r.status_code == 201, r.text
    return client


@pytest.fixture
def user_a(client):
    return signup(client, "a@example.com")


@pytest.fixture
def user_b():
    with TestClient(app) as c:
        yield signup(c, "b@example.com")
