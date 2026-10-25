import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim

STAGES = ["parsed", "chunked", "embedded", "summarized", "key_points", "entities", "verified", "done"]


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("uq_users_email_lower", sql_text("lower(email)"), unique=True),)


class Document(Base):
    """A primary document (owned by a user) or a reference document (shared)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("kind in ('primary','reference')", name="ck_documents_kind"),
        CheckConstraint("(kind = 'primary') = (owner_id is not null)", name="ck_documents_owner"),
        Index("ix_documents_owner_sha", "owner_id", "sha256", "created_at"),
        Index(
            "uq_documents_reference_sha",
            "sha256",
            unique=True,
            postgresql_where=sql_text("kind = 'reference'"),
        ),
    )


class DocumentBlob(Base):
    __tablename__ = "document_blobs"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    original: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary)


class DocumentPage(Base):
    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    doc_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # field | paragraph | table
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section_title: Mapped[str | None] = mapped_column(Text)
    subsection_title: Mapped[str | None] = mapped_column(Text)
    monograph: Mapped[str | None] = mapped_column(Text)
    monograph_key: Mapped[str | None] = mapped_column(String(120))
    field_label: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_doc_ordinal"),
        Index("ix_chunks_doc_monograph", "document_id", "monograph_key"),
        Index(
            "ix_chunks_reference_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=sql_text("doc_kind = 'reference'"),
        ),
    )

    @property
    def section_path(self) -> str:
        parts = [self.section_title, self.monograph, self.field_label or self.subsection_title]
        return " > ".join(p for p in parts if p)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    reference_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    current_stage: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(foreign_keys=[document_id], lazy="joined")
    reference: Mapped[Document] = relationship(foreign_keys=[reference_id], lazy="joined")
    stages: Mapped[list["JobStage"]] = relationship(
        order_by="JobStage.ordinal", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("status in ('queued','running','succeeded','failed')", name="ck_jobs_status"),
        Index("ix_jobs_queue", "status", "created_at"),
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
    )


class JobStage(Base):
    __tablename__ = "job_stages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("job_id", "name", name="uq_job_stages_job_name"),
        CheckConstraint(
            "status in ('pending','running','succeeded','failed','skipped')", name="ck_job_stages_status"
        ),
    )


class Summary(Base):
    __tablename__ = "summaries"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CriticalPoint(Base):
    __tablename__ = "critical_points"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[int | None] = mapped_column(Integer)
    related_drug: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (Index("ix_critical_points_job", "job_id", "rank"),)


class MedicationEntity(Base):
    __tablename__ = "medication_entities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    drug_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dose_value: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[str | None] = mapped_column(String(40))
    route: Mapped[str | None] = mapped_column(String(80))
    frequency: Mapped[str | None] = mapped_column(String(200))
    timing: Mapped[str | None] = mapped_column(String(200))
    duration: Mapped[str | None] = mapped_column(String(200))
    indication: Mapped[str | None] = mapped_column(String(300))
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_quote: Mapped[str | None] = mapped_column(Text)

    verification: Mapped["Verification | None"] = relationship(
        cascade="all, delete-orphan", uselist=False, lazy="joined"
    )

    __table_args__ = (Index("ix_medication_entities_job", "job_id", "ordinal"),)


class Verification(Base):
    __tablename__ = "verifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medication_entities.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    matched_monograph: Mapped[str | None] = mapped_column(String(200))
    name_match: Mapped[str | None] = mapped_column(String(16))
    parameter_checks: Mapped[list | None] = mapped_column(JSONB)
    retrieval: Mapped[dict | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    citations: Mapped[list["VerificationCitation"]] = relationship(
        order_by="VerificationCitation.ordinal", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("status in ('supported','contradicted','unsupported')", name="ck_verifications_status"),
    )


class VerificationCitation(Base):
    __tablename__ = "verification_citations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    verification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verifications.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # supporting | contradicting
    parameter: Mapped[str | None] = mapped_column(String(24))
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str] = mapped_column(Text, nullable=False)
    passage_text: Mapped[str] = mapped_column(Text, nullable=False)

    chunk: Mapped[Chunk] = relationship(lazy="joined")