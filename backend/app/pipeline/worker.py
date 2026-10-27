"""Job worker: claims queued jobs with SKIP LOCKED and runs them.

Run more than one worker process to scale out. A job's lease is extended by a
heartbeat while it runs; a job whose lease expires (worker crash) is re-queued
or, after the attempt limit, failed, so no job can hang forever.
"""

import logging
import os
import signal
import socket
import threading
import uuid

import psycopg
from sqlalchemy import text

from app.config import get_settings
from app.db import session_scope
from app.logging_setup import bind, configure_logging, log
from app.models import Job
from app.pipeline.notify import JOB_QUEUE, notify_job_changed
from app.pipeline.runner import _mark_failed, run_job

logger = logging.getLogger("worker")
settings = get_settings()
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
_stop = threading.Event()


def claim_job() -> uuid.UUID | None:
    with session_scope() as s:
        row = s.execute(
            text(
                """
                UPDATE jobs SET status = 'running', locked_by = :w, attempts = attempts + 1,
                       started_at = COALESCE(started_at, now()),
                       lease_expires_at = now() + make_interval(secs => :lease)
                WHERE id = (SELECT id FROM jobs WHERE status = 'queued'
                            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING id
                """
            ),
            {"w": WORKER_ID, "lease": settings.job_lease_seconds},
        ).first()
        return row[0] if row else None


def reap_expired() -> None:
    """Re-queue or fail jobs whose worker stopped heart-beating."""
    to_fail: list[tuple[uuid.UUID, str | None]] = []
    with session_scope() as s:
        ids = s.execute(
            text("SELECT id FROM jobs WHERE status = 'running' AND lease_expires_at < now() FOR UPDATE SKIP LOCKED")
        ).scalars().all()
        for job_id in ids:
            job = s.get(Job, job_id)
            log(logger, "lease expired", job_id=job_id, attempts=job.attempts)
            if job.attempts >= settings.job_max_attempts:
                to_fail.append((job_id, next((st.name for st in job.stages if st.status == "running"), job.current_stage)))
            else:
                job.status, job.locked_by, job.lease_expires_at = "queued", None, None
                notify_job_changed(s, job_id)
    for job_id, stage in to_fail:
        _mark_failed(job_id, stage or "parsed", "The worker processing this job stopped responding.", 0)


def _heartbeat(job_id: uuid.UUID, done: threading.Event) -> None:
    while not done.wait(settings.job_heartbeat_seconds):
        try:
            with session_scope() as s:
                s.execute(
                    text("UPDATE jobs SET lease_expires_at = now() + make_interval(secs => :l) WHERE id = :id AND locked_by = :w"),
                    {"l": settings.job_lease_seconds, "id": job_id, "w": WORKER_ID},
                )
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat failed")


def process(job_id: uuid.UUID) -> None:
    done = threading.Event()
    hb = threading.Thread(target=_heartbeat, args=(job_id, done), daemon=True)
    hb.start()
    try:
        run_job(job_id)
    finally:
        done.set()


def _listen_dsn() -> str:
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


def main() -> None:
    configure_logging(settings.log_level, settings.log_json)
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    log(logger, "worker started", worker=WORKER_ID)
    conn = psycopg.connect(_listen_dsn(), autocommit=True)
    conn.execute(f"LISTEN {JOB_QUEUE}")
    while not _stop.is_set():
        try:
            reap_expired()
            job_id = claim_job()
            if job_id:
                with bind(worker=WORKER_ID):
                    process(job_id)
                continue
            # Idle: wake on NOTIFY or after the poll interval.
            for _ in conn.notifies(timeout=settings.worker_poll_seconds, stop_after=1):
                break
        except psycopg.OperationalError:
            logger.exception("database connection lost, reconnecting")
            _stop.wait(2)
            conn = psycopg.connect(_listen_dsn(), autocommit=True)
            conn.execute(f"LISTEN {JOB_QUEUE}")
        except Exception:  # noqa: BLE001
            logger.exception("worker loop error")
            _stop.wait(2)
    log(logger, "worker stopped", worker=WORKER_ID)


if __name__ == "__main__":
    main()
