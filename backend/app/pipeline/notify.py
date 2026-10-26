from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

JOB_EVENTS = "job_events"
JOB_QUEUE = "job_queue"


def notify_job_changed(session: Session, job_id: UUID) -> None:
    """Delivered by Postgres when the surrounding transaction commits."""
    session.execute(text("SELECT pg_notify(:ch, :payload)"), {"ch": JOB_EVENTS, "payload": str(job_id)})


def notify_job_queued(session: Session) -> None:
    session.execute(text("SELECT pg_notify(:ch, '')"), {"ch": JOB_QUEUE})
