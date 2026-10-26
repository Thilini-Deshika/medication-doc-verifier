"""Creating an analysis job from an upload.

Everything created for one upload is written in a single transaction:
  documents row, document_blobs row (file bytes), jobs row, one job_stages row
  per pipeline stage, and the queue notification. The bytes live in Postgres
  rather than on disk so that file and rows commit or roll back together.
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingest.pdf import DocumentError, detect_format
from app.models import STAGES, Document, DocumentBlob, Job, JobStage, User
from app.pipeline.notify import notify_job_queued

logger = logging.getLogger(__name__)

CONTENT_TYPES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class UploadError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class UploadResult:
    job: Job
    duplicate: bool


def _find_duplicate(session: Session, user: User, sha256: str, reference_id: UUID, window: int) -> Job | None:
    return session.execute(
        select(Job)
        .join(Document, Document.id == Job.document_id)
        .where(
            Job.owner_id == user.id,
            Document.sha256 == sha256,
            Job.reference_id == reference_id,
            Job.status != "failed",
            Job.created_at > text("now() - make_interval(secs => :w)").bindparams(w=window),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def create_job(session: Session, user: User, filename: str, data: bytes, reference_id: UUID) -> UploadResult:
    settings = get_settings()
    if not data:
        raise UploadError("The file is empty.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise UploadError(f"The file is larger than {settings.max_upload_mb} MB.", 413)
    try:
        fmt = detect_format(data)
    except DocumentError as exc:
        raise UploadError(str(exc), 415) from exc

    reference = session.get(Document, reference_id)
    if reference is None or reference.kind != "reference":
        raise UploadError("Unknown reference document.", 404)

    sha = hashlib.sha256(data).hexdigest()
    try:
        # Serialise concurrent identical uploads by the same user so exactly one job is created.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"{user.id}:{sha}:{reference_id}"},
        )
        existing = _find_duplicate(session, user, sha, reference_id, settings.duplicate_window_seconds)
        if existing is not None:
            session.rollback()
            logger.info("duplicate upload returned existing job", extra={"fields": {"job_id": str(existing.id)}})
            return UploadResult(job=existing, duplicate=True)

        safe_name = os.path.basename(filename or "document")[:300] or "document"
        doc = Document(
            kind="primary",
            owner_id=user.id,
            title=os.path.splitext(safe_name)[0][:300] or safe_name,
            filename=safe_name,
            content_type=CONTENT_TYPES[fmt],
            size_bytes=len(data),
            sha256=sha,
        )
        session.add(doc)
        session.flush()
        session.add(DocumentBlob(document_id=doc.id, original=data))
        job = Job(owner_id=user.id, document_id=doc.id, reference_id=reference_id, status="queued")
        session.add(job)
        session.flush()
        session.add_all(JobStage(job_id=job.id, name=n, ordinal=i, status="pending") for i, n in enumerate(STAGES))
        notify_job_queued(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(job)
    logger.info(
        "job created",
        extra={"fields": {"job_id": str(job.id), "document_id": str(doc.id), "bytes": len(data), "format": fmt}},
    )
    return UploadResult(job=job, duplicate=False)
