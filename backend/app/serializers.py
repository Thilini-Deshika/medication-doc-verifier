from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CriticalPoint, Job, MedicationEntity, Summary


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def job_snapshot(job: Job) -> dict:
    return {
        "id": str(job.id),
        "status": job.status,
        "current_stage": job.current_stage,
        "error": job.error,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "document": {
            "id": str(job.document.id),
            "filename": job.document.filename,
            "title": job.document.title,
            "page_count": job.document.page_count,
        },
        "reference": {"id": str(job.reference.id), "title": job.reference.title},
        "stages": [
            {
                "name": st.name,
                "status": st.status,
                "started_at": iso(st.started_at),
                "finished_at": iso(st.finished_at),
                "duration_ms": st.duration_ms,
                "error": st.error,
                "detail": st.detail,
            }
            for st in job.stages
        ],
    }


def citation_dict(c) -> dict:
    return {
        "id": str(c.id),
        "chunk_id": str(c.chunk_id),
        "role": c.role,
        "parameter": c.parameter,
        "quote": c.quote,
        "page_start": c.page_start,
        "page_end": c.page_end,
        "section_path": c.section_path,
        "passage_text": c.passage_text,
        "reference_id": str(c.chunk.document_id),
    }


def job_result(session: Session, job: Job) -> dict:
    summary = session.get(Summary, job.id)
    points = session.execute(
        select(CriticalPoint).where(CriticalPoint.job_id == job.id).order_by(CriticalPoint.rank)
    ).scalars().all()
    entities = session.execute(
        select(MedicationEntity).where(MedicationEntity.job_id == job.id).order_by(MedicationEntity.ordinal)
    ).scalars().all()
    return {
        "job_id": str(job.id),
        "status": job.status,
        "summary": summary.text if summary else None,
        "critical_points": [
            {
                "id": str(p.id),
                "rank": p.rank,
                "category": p.category,
                "priority": p.priority,
                "text": p.text,
                "source_quote": p.source_quote,
                "source_page": p.source_page,
                "related_drug": p.related_drug,
            }
            for p in points
        ],
        "medications": [
            {
                "id": str(e.id),
                "drug_name": e.drug_name,
                "dose_value": e.dose_value,
                "dose_unit": e.dose_unit,
                "route": e.route,
                "frequency": e.frequency,
                "timing": e.timing,
                "duration": e.duration,
                "indication": e.indication,
                "source_page": e.source_page,
                "source_quote": e.source_quote,
                "verification": None
                if e.verification is None
                else {
                    "status": e.verification.status,
                    "severity": e.verification.severity,
                    "explanation": e.verification.explanation,
                    "matched_monograph": e.verification.matched_monograph,
                    "name_match": e.verification.name_match,
                    "parameter_checks": e.verification.parameter_checks or [],
                    "citations": [citation_dict(c) for c in e.verification.citations],
                },
            }
            for e in entities
        ],
    }


def load_owned_job(session: Session, job_id: UUID, user_id: UUID) -> Job | None:
    """Jobs are only ever loaded through their owner; other users get None (404)."""
    return session.execute(select(Job).where(Job.id == job_id, Job.owner_id == user_id)).unique().scalar_one_or_none()
