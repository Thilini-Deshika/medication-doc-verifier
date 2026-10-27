"""Builders for small synthetic documents. All names and content are invented."""

import functools
import hashlib

import pymupdf

from app.ingest import service as ingest
from app.models import Document, DocumentBlob

BOLD, REG = "hebo", "helv"


class PdfWriter:
    def __init__(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page()
        self.y = 60

    def _room(self):
        if self.y > 780:
            self.page = self.doc.new_page()
            self.y = 60

    def heading(self, text):
        self._room()
        self.page.insert_text((50, self.y), text, fontname=BOLD, fontsize=12)
        self.y += 30

    def field(self, label, body):
        self._room()
        self.page.insert_text((50, self.y), label + ":", fontname=BOLD, fontsize=10)
        x = 50 + pymupdf.get_text_length(label + ": ", fontname=BOLD, fontsize=10)
        self.page.insert_text((x, self.y), body, fontname=REG, fontsize=10)
        self.y += 30

    def para(self, text):
        self._room()
        self.page.insert_text((50, self.y), text, fontname=REG, fontsize=10)
        self.y += 30

    def new_page(self):
        self.page = self.doc.new_page()
        self.y = 60

    def bytes(self) -> bytes:
        return self.doc.tobytes()


@functools.lru_cache
def formulary_pdf() -> bytes:
    w = PdfWriter()
    w.heading("I. Test Agents")
    w.heading("Zorvamide")
    w.field("Drug class", "Fictional sedative.")
    w.field("Standard adult dose", "20 mg orally once daily, at bedtime.")
    w.field("Maximum dose", "40 mg orally once daily.")
    w.field("Prescribing notes", "Zorvamide treatment must not exceed 4 weeks.")
    w.heading("Calmitol")
    w.field("Drug class", "Fictional antihypertensive.")
    w.field("Standard adult dose", "5 mg orally twice daily.")
    w.new_page()
    w.heading("II. Quick Reference")
    w.para("Zorvamide must be taken at bedtime.")
    return w.bytes()


@functools.lru_cache
def primary_pdf() -> bytes:
    w = PdfWriter()
    w.heading("1. Medications")
    w.para("Zorvamide 20 mg orally every morning for 8 weeks.")
    w.para("Calmitol 5 mg orally twice daily, continue.")
    w.para("Xenoflex 10 mg orally once daily.")
    return w.bytes()


def make_reference(db, title: str = "Test Formulary") -> Document:
    data = formulary_pdf()
    doc = Document(
        kind="reference",
        title=title,
        filename="formulary.pdf",
        content_type="application/pdf",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(doc)
    db.flush()
    db.add(DocumentBlob(document_id=doc.id, original=data))
    db.flush()
    ingest.parse_document(db, doc)
    ingest.chunk_document(db, doc)
    ingest.embed_document(db, doc)
    db.commit()
    return doc
