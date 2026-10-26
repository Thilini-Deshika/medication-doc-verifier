"""Targeted retrieval of reference passages for one medication.

Three channels, merged and de-duplicated:
  scoped   every field of the resolved monograph, ranked by similarity to the
           prescription (exact, computed in Python: a monograph has ~10 chunks)
  mention  chunks of other monographs or summary sections that name the drug
           (e.g. quick-reference constraints), found lexically
  global   semantic top-k over the whole reference (HNSW), kept only above the
           relevance floor
"""

import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.ingest.embeddings import Embedder
from app.models import Chunk
from app.verification.resolver import Resolution

logger = logging.getLogger(__name__)

MANDATORY_FIELDS = re.compile(r"dose|dosing|prescrib|indication|duration|administration", re.I)


@dataclass
class Passage:
    id: str
    chunk: Chunk
    score: float | None
    channel: str


@dataclass
class Retrieval:
    resolution: Resolution
    passages: list[Passage] = field(default_factory=list)
    global_hits: list[Passage] = field(default_factory=list)

    def by_id(self) -> dict[str, Passage]:
        return {p.id: p for p in self.passages}


def query_text(entity) -> str:
    bits = [f"Prescribing parameters for {entity.drug_name}."]
    if entity.dose_value is not None:
        dose = f"{entity.dose_value:g} {entity.dose_unit or ''}".strip()
        bits.append(f"Dose {dose}.")
    for label, value in (
        ("Route", entity.route),
        ("Frequency", entity.frequency),
        ("Timing", entity.timing),
        ("Duration", entity.duration),
        ("Indication", entity.indication),
    ):
        if value:
            bits.append(f"{label} {value}.")
    return " ".join(bits)


def monograph_index(session: Session, reference_id: UUID) -> dict[str, str]:
    rows = session.execute(
        select(Chunk.monograph_key, Chunk.monograph)
        .where(Chunk.document_id == reference_id, Chunk.monograph_key.is_not(None))
        .distinct()
    ).all()
    return {k: m for k, m in rows}


def _cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / d) if d else 0.0


def retrieve(
    session: Session,
    reference_id: UUID,
    entity,
    resolution: Resolution,
    embedder: Embedder,
    settings: Settings,
) -> Retrieval:
    qvec = embedder.embed_query(query_text(entity))
    result = Retrieval(resolution=resolution)
    seen: set[UUID] = set()

    def add(chunk: Chunk, score: float | None, channel: str, bucket: list[Passage]):
        if chunk.id in seen:
            return
        seen.add(chunk.id)
        bucket.append(Passage(id="", chunk=chunk, score=score, channel=channel))

    scoped: list[Passage] = []
    if resolution.key:
        rows = session.execute(
            select(Chunk).where(Chunk.document_id == reference_id, Chunk.monograph_key == resolution.key)
        ).scalars().all()
        ranked = sorted(
            ((c, _cosine(qvec, c.embedding)) for c in rows if c.embedding is not None),
            key=lambda t: t[1],
            reverse=True,
        )
        for i, (c, s) in enumerate(ranked):
            if i < settings.retrieval_scoped_k or MANDATORY_FIELDS.search(c.field_label or ""):
                add(c, s, "scoped", scoped)

    mentions: list[Passage] = []
    name = entity.drug_name.strip()
    if name:
        pattern = r"\m" + re.escape(name) + r"\M"
        rows = session.execute(
            select(Chunk)
            .where(
                Chunk.document_id == reference_id,
                Chunk.text.op("~*")(pattern),
                (Chunk.monograph_key.is_(None)) | (Chunk.monograph_key != (resolution.key or "")),
            )
            .order_by(Chunk.ordinal)
            .limit(settings.retrieval_mention_k)
        ).scalars().all()
        for c in rows:
            add(c, _cosine(qvec, c.embedding) if c.embedding is not None else None, "mention", mentions)

    global_hits: list[Passage] = []
    session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    distance = Chunk.embedding.cosine_distance(qvec)
    rows = session.execute(
        select(Chunk, distance.label("d"))
        .where(Chunk.doc_kind == "reference", Chunk.document_id == reference_id, Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(settings.retrieval_global_k + 4)
    ).all()
    for c, d in rows:
        score = 1.0 - float(d)
        if score >= settings.min_relevance and len(global_hits) < settings.retrieval_global_k:
            add(c, score, "global", global_hits)

    result.passages = scoped + mentions + global_hits
    for i, p in enumerate(result.passages, start=1):
        p.id = f"P{i}"
    result.global_hits = global_hits
    return result
