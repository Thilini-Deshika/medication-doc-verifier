"""Executes one job stage by stage with per-stage status, timing and error capture."""

import logging
import time
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.db import session_scope
from app.ingest.embeddings import get_embedder
from app.ingest.pdf import DocumentError
from app.llm.base import LLMError, get_llm
from app.logging_setup import bind, log
from app.models import STAGES, Job
from app.pipeline.notify import notify_job_changed
from app.pipeline.stages import STAGE_FUNCS, StageContext

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_message(stage: str, exc: Exception) -> tuple[str, str | None]:
    """(message safe to show, internal error id if the failure was unexpected)."""
    if isinstance(exc, (DocumentError, LLMError)):
        return str(exc), None
    error_id = uuid.uuid4().hex[:8]
    return f"Unexpected error while running stage '{stage}'. Reference {error_id}.", error_id


def run_job(job_id: uuid.UUID, llm=None, embedder=None) -> str:
    """Run all remaining stages. Returns the final job status."""
    settings = get_settings()
    with bind(job_id=job_id):
        with session_scope() as s:
            job = s.get(Job, job_id)
            document_id = job.document_id
            pending = [st.name for st in job.stages if st.status != "succeeded"]
        log(logger, "job started", document_id=document_id, remaining_stages=pending)
        llm = llm or get_llm()
        embedder = embedder or get_embedder()

        for name in STAGES:
            if name not in pending:
                continue
            with bind(stage=name, document_id=document_id):
                started = time.perf_counter()
                with session_scope() as s:
                    job = s.get(Job, job_id)
                    stage = next(st for st in job.stages if st.name == name)
                    stage.status, stage.started_at, stage.finished_at = "running", _now(), None
                    stage.error = stage.duration_ms = None
                    job.current_stage = name
                    notify_job_changed(s, job_id)
                log(logger, "stage started")
                try:
                    with session_scope() as s:
                        job = s.get(Job, job_id)
                        ctx = StageContext(session=s, job=job, settings=settings, llm=llm, embedder=embedder)
                        detail = STAGE_FUNCS[name](ctx)
                        stage = next(st for st in job.stages if st.name == name)
                        stage.status = "succeeded"
                        stage.finished_at = _now()
                        stage.duration_ms = int((time.perf_counter() - started) * 1000)
                        stage.detail = detail
                        if name == STAGES[-1]:
                            job.status, job.finished_at, job.error = "succeeded", _now(), None
                            job.lease_expires_at = job.locked_by = None
                        notify_job_changed(s, job_id)
                    log(logger, "stage succeeded", duration_ms=int((time.perf_counter() - started) * 1000), detail=detail)
                except Exception as exc:  # noqa: BLE001 - every failure must be recorded, never left hanging
                    message, error_id = _user_message(name, exc)
                    log(
                        logger,
                        "stage failed",
                        level=logging.ERROR,
                        error=message,
                        error_id=error_id,
                        error_type=type(exc).__name__,
                    )
                    if error_id:
                        logger.exception("stage exception", extra={"fields": {"error_id": error_id}})
                    _mark_failed(job_id, name, message, int((time.perf_counter() - started) * 1000))
                    return "failed"
        return "succeeded"


def _mark_failed(job_id: uuid.UUID, stage_name: str, message: str, duration_ms: int) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        reached = False
        for st in job.stages:
            if st.name == stage_name:
                st.status, st.error, st.finished_at, st.duration_ms = "failed", message, _now(), duration_ms
                reached = True
            elif reached and st.status != "succeeded":
                st.status = "skipped"
        job.status, job.error, job.finished_at = "failed", message, _now()
        job.lease_expires_at = job.locked_by = None
        notify_job_changed(s, job_id)
