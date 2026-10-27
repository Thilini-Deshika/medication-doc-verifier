import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import current_user
from app.events import bus
from app.models import DocumentBlob, Job, User
from app.pipeline.notify import notify_job_changed, notify_job_queued
from app.serializers import job_result, job_snapshot, load_owned_job
from app.services.uploads import UploadError, create_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
TERMINAL = {"succeeded", "failed"}


def _owned_or_404(db: Session, job_id: UUID, user: User) -> Job:
    job = load_owned_job(db, job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload(
    response: Response,
    file: UploadFile = File(...),
    reference_id: UUID = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    try:
        result = await asyncio.to_thread(create_job, db, user, file.filename or "document", data, reference_id)
    except UploadError as exc:
        raise HTTPException(exc.status_code, str(exc))
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return {"duplicate": result.duplicate, "job": job_snapshot(result.job)}


@router.get("")
def list_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    jobs = db.execute(
        select(Job).where(Job.owner_id == user.id).order_by(Job.created_at.desc()).limit(50)
    ).unique().scalars().all()
    return [job_snapshot(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return job_snapshot(_owned_or_404(db, job_id, user))


@router.get("/{job_id}/result")
def get_result(job_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return job_result(db, _owned_or_404(db, job_id, user))


@router.get("/{job_id}/document")
def get_document(job_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _owned_or_404(db, job_id, user)
    blob = db.get(DocumentBlob, job.document_id)
    if blob is None or blob.pdf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document is not ready yet")
    return Response(blob.pdf, media_type="application/pdf", headers={"Cache-Control": "private, max-age=600"})


@router.post("/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry(job_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _owned_or_404(db, job_id, user)
    if job.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed jobs can be retried")
    for st in job.stages:
        if st.status in ("failed", "skipped"):
            st.status, st.error, st.started_at, st.finished_at, st.duration_ms, st.detail = "pending", None, None, None, None, None
    job.status, job.error, job.attempts, job.finished_at = "queued", None, 0, None
    notify_job_changed(db, job.id)
    notify_job_queued(db)
    db.commit()
    return job_snapshot(job)


def _snapshot(job_id: UUID, user_id: UUID) -> dict | None:
    with SessionLocal() as db:
        job = load_owned_job(db, job_id, user_id)
        return job_snapshot(job) if job else None


@router.get("/{job_id}/events")
async def events(job_id: UUID, user: User = Depends(current_user)):
    """Server-sent events: a full job snapshot on connect and after every change."""
    first = await asyncio.to_thread(_snapshot, job_id, user.id)
    if first is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    key = str(job_id)

    async def stream():
        queue = bus.subscribe(key)
        try:
            snap, last = first, None
            while True:
                payload = json.dumps(snap)
                if payload != last:
                    yield f"event: job\ndata: {payload}\n\n"
                    last = payload
                if snap["status"] in TERMINAL:
                    return
                try:
                    await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                snap = await asyncio.to_thread(_snapshot, job_id, user.id)
                if snap is None:
                    return
        finally:
            bus.unsubscribe(key, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
