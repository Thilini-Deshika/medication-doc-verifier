"""Parse, chunk and embed one document. Shared by the job pipeline and the reference loader."""

import logging
import time

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingest.chunking import chunk_pages, embedding_input
from app.ingest.embeddings import get_embedder
from app.ingest.pdf import detect_format, extract_pages, to_pdf
from app.models import Chunk, Document, DocumentBlob, DocumentPage

logger = logging.getLogger(__name__)


def parse_document(session: Session, doc: Document) -> dict:
    blob = session.get(DocumentBlob, doc.id)
    fmt = detect_format(blob.original)
    started = time.perf_counter()
    if blob.pdf is None:
        blob.pdf = to_pdf(blob.original)
    pages = extract_pages(blob.pdf)
    session.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))
    session.add_all(DocumentPage(document_id=doc.id, page_number=p.number, text=p.text) for p in pages)
    doc.page_count = len(pages)
    session.flush()
    return {
        "source_format": fmt,
        "converted_to_pdf": fmt == "docx",
        "pages": len(pages),
        "characters": sum(len(p.text) for p in pages),
        "ms": int((time.perf_counter() - started) * 1000),
    }


def chunk_document(session: Session, doc: Document) -> dict:
    s = get_settings()
    blob = session.get(DocumentBlob, doc.id)
    pages = extract_pages(blob.pdf)
    drafts = chunk_pages(
        pages,
        detect_monographs=doc.kind == "reference",
        max_words=s.chunk_max_words,
        overlap=s.chunk_overlap_words,
    )
    session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    session.add_all(
        Chunk(document_id=doc.id, doc_kind=doc.kind, **{k: getattr(d, k) for k in d.__dataclass_fields__})
        for d in drafts
    )
    session.flush()
    kinds: dict[str, int] = {}
    for d in drafts:
        kinds[d.kind] = kinds.get(d.kind, 0) + 1
    monographs = sorted({d.monograph for d in drafts if d.monograph})
    detail = {"chunks": len(drafts), "by_kind": kinds}
    if monographs:
        detail["monographs"] = monographs
    return detail


def embed_document(session: Session, doc: Document, batch_size: int = 64) -> dict:
    embedder = get_embedder()
    started = time.perf_counter()
    rows = session.execute(
        select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal)
    ).scalars().all()
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        vectors = embedder.embed_documents([embedding_input(c) for c in batch])
        for chunk, vec in zip(batch, vectors):
            chunk.embedding = vec
            chunk.embedding_model = embedder.model_name
        total += len(batch)
    session.flush()
    return {"embedded": total, "model": embedder.model_name, "dim": embedder.dim, "ms": int((time.perf_counter() - started) * 1000)}


def reference_status(session: Session, reference_id) -> dict:
    """How much of a reference document is ingested and which model embedded it."""
    base = Chunk.document_id == reference_id
    chunks = session.execute(select(func.count()).select_from(Chunk).where(base)).scalar_one()
    embedded = session.execute(
        select(func.count()).select_from(Chunk).where(base, Chunk.embedding.is_not(None))
    ).scalar_one()
    monographs = session.execute(
        select(func.count(func.distinct(Chunk.monograph_key))).where(base, Chunk.monograph_key.is_not(None))
    ).scalar_one()
    models = sorted(
        session.execute(select(Chunk.embedding_model).where(base, Chunk.embedding_model.is_not(None)).distinct())
        .scalars()
        .all()
    )
    return {"chunks": chunks, "embedded": embedded, "monographs": monographs, "models": models}


def has_embeddings(session: Session, doc: Document) -> bool:
    missing = session.execute(
        select(Chunk.id).where(Chunk.document_id == doc.id, Chunk.embedding.is_(None)).limit(1)
    ).first()
    return missing is None
