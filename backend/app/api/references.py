from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import Document, DocumentBlob, User

router = APIRouter(prefix="/api/references", tags=["references"])


@router.get("")
def list_references(_: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Document).where(Document.kind == "reference", Document.page_count.is_not(None)).order_by(Document.title)
    ).scalars().all()
    return [{"id": str(d.id), "title": d.title, "page_count": d.page_count} for d in rows]


@router.get("/{reference_id}/file")
def reference_file(reference_id: UUID, _: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = db.get(Document, reference_id)
    blob = db.get(DocumentBlob, reference_id) if doc and doc.kind == "reference" else None
    if blob is None or blob.pdf is None:
        raise HTTPException(404, "Reference not found")
    return Response(blob.pdf, media_type="application/pdf", headers={"Cache-Control": "private, max-age=3600"})
