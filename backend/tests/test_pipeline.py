from sqlalchemy import select

from app.llm.base import LLMError
from app.llm.schemas import (
    CriticalPointOut,
    CriticalPointsOut,
    EntitiesOut,
    MedicationOut,
    SummaryOut,
    VerificationOut,
    ParameterCheckOut,
)
from app.models import Chunk, Document, Job, MedicationEntity, User
from app.pipeline.runner import run_job
from app.ingest.embeddings import get_embedder
from app.services.uploads import create_job
from tests.fakes import ScriptedLLM
from tests.helpers import primary_pdf


def verification_answer(user):
    if "Xenoflex" in user:
        raise AssertionError("unsupported drugs must not reach the LLM")
    pid = next(l for l in user.split("\n\n") if "Standard adult dose" in l).split("]")[0].lstrip("[")
    if "Zorvamide" in user:
        checks = [
            ParameterCheckOut(parameter="timing", prescribed="every morning", reference="at bedtime", result="mismatch", severity="high", passage_id=pid, quote="at bedtime", explanation="x"),
        ]
    else:
        checks = [
            ParameterCheckOut(parameter="dose", prescribed="5 mg", reference="5 mg", result="match", severity="none", passage_id=pid, quote="5 mg orally twice daily", explanation="x"),
        ]
    return VerificationOut(parameter_checks=checks, summary="done")


def good_llm():
    return ScriptedLLM(
        {
            SummaryOut: SummaryOut(summary="A short summary."),
            CriticalPointsOut: CriticalPointsOut(
                points=[
                    CriticalPointOut(text="Take Zorvamide in the morning for 8 weeks.", category="duration_limit", priority=5, source_quote="Zorvamide 20 mg orally every morning for 8 weeks", related_drug="Zorvamide"),
                    CriticalPointOut(text="Invented advice.", category="other_warning", priority=5, source_quote="Drink three litres of water a day"),
                ]
            ),
            EntitiesOut: EntitiesOut(
                medications=[
                    MedicationOut(drug_name="Zorvamide", dose_value=20, dose_unit="mg", route="PO", frequency="every morning", duration="8 weeks", source_quote="Zorvamide 20 mg orally every morning"),
                    MedicationOut(drug_name="Calmitol", dose_value=5, dose_unit="mg", route="PO", frequency="twice daily"),
                    MedicationOut(drug_name="Xenoflex", dose_value=10, dose_unit="mg", route="PO", frequency="once daily"),
                ]
            ),
            VerificationOut: verification_answer,
        }
    )


def make_job(db, reference):
    user = User(email="p@example.com", password_hash="x")
    db.add(user)
    db.commit()
    return create_job(db, user, "discharge.pdf", primary_pdf(), reference.id).job


def test_full_pipeline_produces_grounded_results(db, reference):
    job = make_job(db, reference)
    assert run_job(job.id, llm=good_llm(), embedder=get_embedder()) == "succeeded"

    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == "succeeded"
    assert [s.status for s in job.stages] == ["succeeded"] * 8
    assert all(s.duration_ms is not None and s.started_at and s.finished_at for s in job.stages)

    ents = {e.drug_name: e for e in db.execute(select(MedicationEntity).where(MedicationEntity.job_id == job.id)).scalars().all()}
    assert ents["Zorvamide"].verification.status == "contradicted"
    assert ents["Calmitol"].verification.status == "supported"
    assert ents["Xenoflex"].verification.status == "unsupported"
    assert ents["Zorvamide"].source_page == 1

    from app.serializers import job_result

    result = job_result(db, job)
    assert [p["text"] for p in result["critical_points"]] == ["Take Zorvamide in the morning for 8 weeks."]
    stage = {s.name: s for s in job.stages}["key_points"]
    assert stage.detail["rejected_ungrounded"] == 1

    by_name = {s.name: s for s in job.stages}
    assert by_name["chunked"].detail["reference"]["monographs"] == 2
    assert by_name["embedded"].detail["reference"]["model"] == get_embedder().model_name


def test_failure_in_a_stage_is_recorded_and_job_does_not_hang(db, reference):
    job = make_job(db, reference)
    llm = good_llm()
    llm.fail_on = SummaryOut
    assert run_job(job.id, llm=llm, embedder=get_embedder()) == "failed"

    db.expire_all()
    job = db.get(Job, job.id)
    by_name = {s.name: s for s in job.stages}
    assert job.status == "failed" and "simulated provider outage" in job.error
    assert by_name["embedded"].status == "succeeded"
    assert by_name["summarized"].status == "failed" and by_name["summarized"].error
    assert all(by_name[n].status == "skipped" for n in ("key_points", "entities", "verified", "done"))


def test_retry_resumes_from_the_failed_stage(db, reference):
    job = make_job(db, reference)
    llm = good_llm()
    llm.fail_on = SummaryOut
    run_job(job.id, llm=llm, embedder=get_embedder())

    db.expire_all()
    job = db.get(Job, job.id)
    for st in job.stages:
        if st.status in ("failed", "skipped"):
            st.status = "pending"
    job.status = "queued"
    db.commit()

    llm2 = good_llm()
    assert run_job(job.id, llm=llm2, embedder=get_embedder()) == "succeeded"
    assert "summary" in llm2.calls[0]  # earlier stages were not repeated
    db.expire_all()
    assert db.get(Job, job.id).status == "succeeded"


def test_unreadable_document_fails_the_first_stage_with_a_clear_message(db, reference):
    user = User(email="q@example.com", password_hash="x")
    db.add(user)
    db.commit()
    blank = __import__("pymupdf").open()
    blank.new_page()
    job = create_job(db, user, "blank.pdf", blank.tobytes(), reference.id).job
    assert run_job(job.id, llm=good_llm(), embedder=get_embedder()) == "failed"
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.stages[0].name == "parsed" and job.stages[0].status == "failed"
    assert "No extractable text" in job.error


def test_job_fails_early_when_the_reference_was_never_loaded(db, reference):
    empty = Document(
        kind="reference", title="Not loaded", filename="x.pdf", content_type="application/pdf", size_bytes=1, sha256="0" * 64
    )
    db.add(empty)
    db.commit()
    job = make_job(db, empty)
    assert run_job(job.id, llm=good_llm(), embedder=get_embedder()) == "failed"
    db.expire_all()
    job = db.get(Job, job.id)
    by_name = {s.name: s for s in job.stages}
    assert by_name["parsed"].status == "succeeded"
    assert by_name["chunked"].status == "failed"
    assert 'The reference "Not loaded" has no chunks' in job.error


def test_job_fails_when_reference_was_embedded_with_another_model(db, reference):
    db.execute(Chunk.__table__.update().where(Chunk.document_id == reference.id).values(embedding_model="old-model"))
    db.commit()
    job = make_job(db, reference)
    assert run_job(job.id, llm=good_llm(), embedder=get_embedder()) == "failed"
    db.expire_all()
    job = db.get(Job, job.id)
    assert {s.name: s for s in job.stages}["embedded"].status == "failed"
    assert "Re-ingest the reference" in job.error
