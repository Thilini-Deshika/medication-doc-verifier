import contextlib
from collections.abc import Iterator

from pgvector.psycopg import register_vector
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)


@event.listens_for(engine, "connect")
def _register_vector(dbapi_connection, _record):
    # The vector type only exists after the first migration has run.
    try:
        register_vector(dbapi_connection)
    except Exception:
        dbapi_connection.rollback()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit on success, roll back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session
