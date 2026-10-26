"""Command line utilities: seed, add-reference, eval-retrieval."""

import argparse
import hashlib
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.eval.retrieval_eval import run_eval
from app.ingest import service as ingest
from app.ingest.pdf import detect_format
from app.logging_setup import configure_logging, log
from app.models import Document, DocumentBlob, User
from app.security import hash_password
from app.services.uploads import CONTENT_TYPES

logger = logging.getLogger("cli")


def ensure_demo_user() -> None:
    s = get_settings()
    if not s.seed_demo_user:
        log(logger, "demo user disabled (SEED_DEMO_USER=false)")
        return
    with session_scope() as db:
        exists = db.execute(select(User.id).where(func.lower(User.email) == s.demo_email.lower())).first()
        if exists:
            log(logger, "demo user already present", email=s.demo_email)
            return
        db.add(User(email=s.demo_email.lower(), password_hash=hash_password(s.demo_password)))
        log(logger, "demo user created", email=s.demo_email)


def add_reference(path: Path, title: str | None = None) -> None:
    data = path.read_bytes()
    fmt = detect_format(data)
    sha = hashlib.sha256(data).hexdigest()
    with session_scope() as db:
        doc = db.execute(
            select(Document).where(Document.kind == "reference", Document.sha256 == sha)
        ).scalar_one_or_none()
        if doc is None:
            doc = Document(
                kind="reference",
                title=title or path.stem.replace("_", " ").replace("-", " ").strip().title(),
                filename=path.name,
                content_type=CONTENT_TYPES[fmt],
                size_bytes=len(data),
                sha256=sha,
            )
            db.add(doc)
            db.flush()
            db.add(DocumentBlob(document_id=doc.id, original=data))
            db.flush()
        elif doc.page_count is not None and ingest.has_embeddings(db, doc):
            log(logger, "reference already ingested", title=doc.title, document_id=doc.id)
            return
        log(logger, "ingesting reference", title=doc.title, document_id=doc.id)
        for name, fn in (("parse", ingest.parse_document), ("chunk", ingest.chunk_document), ("embed", ingest.embed_document)):
            detail = fn(db, doc)
            db.flush()
            log(logger, f"reference {name} done", document_id=doc.id, **{k: v for k, v in detail.items() if k != "monographs"})


def seed(reference_dir: str | None = None) -> None:
    s = get_settings()
    ensure_demo_user()
    folder = Path(reference_dir or s.reference_dir)
    files = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".pdf", ".docx")) if folder.is_dir() else []
    if not files:
        log(
            logger,
            "no reference documents found; add .pdf or .docx files to the references folder and run the seed again",
            level=logging.WARNING,
            folder=str(folder),
        )
    for path in files:
        add_reference(path)


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed", help="create the demo user and ingest reference documents")
    p_seed.add_argument("--references", help="folder with reference PDF/DOCX files")
    p_add = sub.add_parser("add-reference", help="ingest one reference document")
    p_add.add_argument("path", type=Path)
    p_add.add_argument("--title")
    p_eval = sub.add_parser("eval-retrieval", help="measure retrieval quality on a reference document")
    p_eval.add_argument("--reference-title", help="title of the reference (default: the only one)")
    p_eval.add_argument("--gold", type=Path, help="optional JSON file of hand-labelled queries")
    p_eval.add_argument("--k", type=int, default=5)
    args = parser.parse_args(argv)

    if args.cmd == "seed":
        seed(args.references)
    elif args.cmd == "add-reference":
        add_reference(args.path, args.title)
    elif args.cmd == "eval-retrieval":
        return run_eval(args.reference_title, args.gold, args.k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
