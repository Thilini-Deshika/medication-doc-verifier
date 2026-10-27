from sqlalchemy import select

from app.ingest.chunking import chunk_pages
from app.ingest.pdf import extract_pages
from app.models import Chunk
from tests.helpers import formulary_pdf


def test_monographs_are_split_into_labelled_fields():
    chunks = chunk_pages(extract_pages(formulary_pdf()), detect_monographs=True)
    dose = [c for c in chunks if c.monograph == "Zorvamide" and c.field_label == "Standard adult dose"]
    assert len(dose) == 1
    assert dose[0].section_title == "I. Test Agents"
    assert dose[0].page_start == 1
    assert "at bedtime" in dose[0].text
    assert {c.monograph for c in chunks if c.monograph} == {"Zorvamide", "Calmitol"}


def test_text_after_monographs_is_not_attributed_to_a_monograph():
    chunks = chunk_pages(extract_pages(formulary_pdf()), detect_monographs=True)
    quick = [c for c in chunks if "must be taken at bedtime" in c.text]
    assert quick and quick[0].monograph is None
    assert quick[0].section_title == "II. Quick Reference"
    assert quick[0].page_start == 2


def test_chunks_are_embedded_and_stored(db, reference):
    rows = db.execute(select(Chunk).where(Chunk.document_id == reference.id)).scalars().all()
    assert rows and all(r.embedding is not None for r in rows)
    assert all(r.doc_kind == "reference" for r in rows)
