"""The eight pipeline stages. Each takes a StageContext and returns a detail dict.

Stages read their inputs from the database and write their outputs in the
caller's transaction, deleting any earlier output first, so a stage can be
re-run safely after a failure.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.grounding import locate, normalize, page_mentioning
from app.ingest import service as ingest
from app.ingest.pdf import DocumentError
from app.ingest.embeddings import Embedder
from app.llm.base import LLM
from app.llm import prompts
from app.llm.schemas import CriticalPointsOut, EntitiesOut, SummaryOut
from app.models import (
    CriticalPoint,
    DocumentPage,
    Job,
    MedicationEntity,
    Summary,
    Verification,
    VerificationCitation,
)
from app.verification.resolver import resolve
from app.verification.retrieval import monograph_index, retrieve
from app.verification.verifier import VerificationResult, verify_entity

logger = logging.getLogger(__name__)


@dataclass
class StageContext:
    session: Session
    job: Job
    settings: Settings
    llm: LLM
    embedder: Embedder


def _pages(session: Session, document_id: UUID) -> dict[int, str]:
    rows = session.execute(
        select(DocumentPage.page_number, DocumentPage.text)
        .where(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_number)
    ).all()
    return {n: t for n, t in rows}


def _windows(pages: dict[int, str], max_chars: int) -> list[str]:
    """Group pages into prompts no longer than max_chars, with page markers."""
    windows: list[str] = []
    cur: list[str] = []
    size = 0
    for n, text in pages.items():
        piece = f"--- Page {n} ---\n{text}"
        if cur and size + len(piece) > max_chars:
            windows.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(piece)
        size += len(piece)
    if cur:
        windows.append("\n\n".join(cur))
    return windows


def stage_parsed(ctx: StageContext) -> dict:
    return ingest.parse_document(ctx.session, ctx.job.document)


def stage_chunked(ctx: StageContext) -> dict:
    # Fail fast, before any work, if the selected reference was never loaded.
    reference = ctx.job.reference
    ref = ingest.reference_status(ctx.session, reference.id)
    if ref["chunks"] == 0:
        raise DocumentError(
            f'The reference "{reference.title}" has no chunks. Load it with the seed command (see the README) and retry.'
        )
    detail = ingest.chunk_document(ctx.session, ctx.job.document)
    detail["reference"] = {"title": reference.title, "chunks": ref["chunks"], "monographs": ref["monographs"]}
    return detail


def stage_embedded(ctx: StageContext) -> dict:
    detail = ingest.embed_document(ctx.session, ctx.job.document)
    reference = ctx.job.reference
    ref = ingest.reference_status(ctx.session, reference.id)
    if ref["embedded"] < ref["chunks"]:
        raise DocumentError(
            f'The reference "{reference.title}" has {ref["chunks"] - ref["embedded"]} chunks without embeddings. '
            "Run the seed command again and retry."
        )
    if ref["models"] != [ctx.embedder.model_name]:
        raise DocumentError(
            f'The reference "{reference.title}" was embedded with {ref["models"]}, but the current embedding model is '
            f"{ctx.embedder.model_name}. Re-ingest the reference with the seed command and retry."
        )
    detail["reference"] = {"title": reference.title, "chunks": ref["chunks"], "model": ctx.embedder.model_name}
    return detail


def stage_summarized(ctx: StageContext) -> dict:
    windows = _windows(_pages(ctx.session, ctx.job.document_id), ctx.settings.long_doc_chars)
    if len(windows) == 1:
        text = ctx.llm.generate(
            system=prompts.SUMMARY_SYSTEM, user=windows[0], output=SummaryOut, purpose="summary"
        ).summary
    else:
        parts = [
            ctx.llm.generate(
                system=prompts.SUMMARY_SYSTEM, user=w, output=SummaryOut, purpose="summary_part"
            ).summary
            for w in windows
        ]
        text = ctx.llm.generate(
            system=prompts.SUMMARY_COMBINE_SYSTEM,
            user="\n\n".join(f"Part {i + 1}:\n{p}" for i, p in enumerate(parts)),
            output=SummaryOut,
            purpose="summary_combine",
        ).summary
    ctx.session.execute(delete(Summary).where(Summary.job_id == ctx.job.id))
    ctx.session.add(Summary(job_id=ctx.job.id, text=text.strip(), model=ctx.llm.model))
    return {"words": len(text.split()), "windows": len(windows)}


def stage_key_points(ctx: StageContext) -> dict:
    pages = _pages(ctx.session, ctx.job.document_id)
    raw = []
    for w in _windows(pages, ctx.settings.long_doc_chars):
        raw.extend(
            ctx.llm.generate(
                system=prompts.CRITICAL_SYSTEM, user=w, output=CriticalPointsOut, purpose="critical_points"
            ).points
        )
    kept, rejected, seen = [], 0, set()
    for p in raw:
        found = locate(p.source_quote, pages, ctx.settings.quote_match_threshold)
        if found is None:
            rejected += 1
            logger.warning(
                "critical point rejected: quote not found in document",
                extra={"fields": {"category": p.category}},
            )
            continue
        key = normalize(p.source_quote)
        if key in seen:
            continue
        seen.add(key)
        kept.append((p, found[0]))
    kept.sort(key=lambda t: (-min(max(t[0].priority, 1), 5), t[1]))
    kept = kept[:12]
    ctx.session.execute(delete(CriticalPoint).where(CriticalPoint.job_id == ctx.job.id))
    for rank, (p, page) in enumerate(kept, start=1):
        ctx.session.add(
            CriticalPoint(
                job_id=ctx.job.id,
                rank=rank,
                category=p.category,
                priority=min(max(p.priority, 1), 5),
                text=p.text.strip(),
                source_quote=p.source_quote.strip(),
                source_page=page,
                related_drug=p.related_drug,
            )
        )
    return {"points": len(kept), "rejected_ungrounded": rejected}


def stage_entities(ctx: StageContext) -> dict:
    pages = _pages(ctx.session, ctx.job.document_id)
    raw = []
    for w in _windows(pages, ctx.settings.long_doc_chars):
        raw.extend(
            ctx.llm.generate(
                system=prompts.ENTITY_SYSTEM, user=w, output=EntitiesOut, purpose="entities"
            ).medications
        )
    ctx.session.execute(delete(MedicationEntity).where(MedicationEntity.job_id == ctx.job.id))
    seen, count = set(), 0
    for m in raw:
        key = (normalize(m.drug_name), m.dose_value, normalize(m.frequency or ""))
        if key in seen:
            continue
        seen.add(key)
        page = page_mentioning(m.drug_name, pages) or m.source_page
        if m.source_quote:
            found = locate(m.source_quote, pages, ctx.settings.quote_match_threshold)
            if found:
                page = found[0]
        ctx.session.add(
            MedicationEntity(
                job_id=ctx.job.id,
                ordinal=count,
                drug_name=m.drug_name.strip(),
                dose_value=m.dose_value,
                dose_unit=m.dose_unit,
                route=m.route,
                frequency=m.frequency,
                timing=m.timing,
                duration=m.duration,
                indication=m.indication,
                source_page=page,
                source_quote=m.source_quote,
            )
        )
        count += 1
    return {"medications": count}


def stage_verified(ctx: StageContext) -> dict:
    s = ctx.settings
    entities = ctx.session.execute(
        select(MedicationEntity).where(MedicationEntity.job_id == ctx.job.id).order_by(MedicationEntity.ordinal)
    ).scalars().all()
    for e in entities:
        if e.verification:
            ctx.session.delete(e.verification)
    ctx.session.flush()
    reference = ctx.job.reference
    index = monograph_index(ctx.session, reference.id)

    prepared = []
    for e in entities:
        resolution = resolve(e.drug_name, index, s.fuzzy_name_threshold)
        retrieval = retrieve(ctx.session, reference.id, e, resolution, ctx.embedder, s)
        logger.info(
            "retrieval",
            extra={
                "fields": {
                    "drug": e.drug_name,
                    "resolution": resolution.status,
                    "monograph": resolution.monograph,
                    "passages": [(p.id, p.channel, None if p.score is None else round(p.score, 3)) for p in retrieval.passages],
                }
            },
        )
        prepared.append((e, retrieval))

    def run(item) -> VerificationResult:
        e, retrieval = item
        return verify_entity(ctx.llm, e, retrieval, reference.title, s)

    with ThreadPoolExecutor(max_workers=max(1, s.verify_concurrency)) as pool:
        results = list(pool.map(run, prepared))

    counts = {"supported": 0, "contradicted": 0, "unsupported": 0}
    for (e, _), r in zip(prepared, results):
        counts[r.status] += 1
        logger.info(
            "verification",
            extra={
                "fields": {
                    "drug": e.drug_name,
                    "status": r.status,
                    "severity": r.severity,
                    "checks": [(c["parameter"], c["result"]) for c in r.parameter_checks],
                }
            },
        )
        v = Verification(
            entity_id=e.id,
            status=r.status,
            severity=r.severity,
            explanation=r.explanation,
            matched_monograph=r.matched_monograph,
            name_match=r.name_match,
            parameter_checks=r.parameter_checks,
            retrieval=r.retrieval,
            model=ctx.llm.model,
        )
        ctx.session.add(v)
        ctx.session.flush()
        for i, c in enumerate(r.citations):
            ch = c["chunk"]
            ctx.session.add(
                VerificationCitation(
                    verification_id=v.id,
                    chunk_id=ch.id,
                    ordinal=i,
                    role=c["role"],
                    parameter=c["parameter"],
                    quote=c["quote"],
                    page_start=ch.page_start,
                    page_end=ch.page_end,
                    section_path=ch.section_path,
                    passage_text=ch.text,
                )
            )
    return counts


def stage_done(ctx: StageContext) -> dict:
    total = ctx.session.execute(
        select(func.count()).select_from(MedicationEntity).where(MedicationEntity.job_id == ctx.job.id)
    ).scalar_one()
    return {"medications_verified": total}


STAGE_FUNCS: dict[str, Callable[[StageContext], dict]] = {
    "parsed": stage_parsed,
    "chunked": stage_chunked,
    "embedded": stage_embedded,
    "summarized": stage_summarized,
    "key_points": stage_key_points,
    "entities": stage_entities,
    "verified": stage_verified,
    "done": stage_done,
}
