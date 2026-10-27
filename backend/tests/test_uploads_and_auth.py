import io

import pytest
from sqlalchemy import func, select, text

from app.models import Document, DocumentBlob, Job, JobStage
from app.models import User
from app.services import uploads
from app.services.uploads import UploadError, create_job
from tests.helpers import primary_pdf


def upload(client, reference, data=None, name="d.pdf"):
    return client.post(
        "/api/jobs",
        files={"file": (name, io.BytesIO(data if data is not None else primary_pdf()), "application/pdf")},
        data={"reference_id": str(reference.id)},
    )


def count(db, model):
    return db.execute(select(func.count()).select_from(model)).scalar_one()


# ---- upload behaviour -------------------------------------------------------


def test_upload_returns_immediately_with_a_queued_job(user_a, reference):
    r = upload(user_a, reference)
    assert r.status_code == 202
    body = r.json()
    assert body["duplicate"] is False
    assert body["job"]["status"] == "queued"
    assert [s["name"] for s in body["job"]["stages"]] == [
        "parsed", "chunked", "embedded", "summarized", "key_points", "entities", "verified", "done",
    ]


def test_upload_writes_all_records_or_none(db, reference, monkeypatch):
    user = User(email="t@example.com", password_hash="x")
    db.add(user)
    db.commit()
    before = {m: count(db, m) for m in (Document, DocumentBlob, Job, JobStage)}

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure after the job row was written")

    # fail after the document, blob, job and stage rows have all been flushed
    monkeypatch.setattr(uploads, "notify_job_queued", boom)
    with pytest.raises(RuntimeError):
        create_job(db, user, "x.pdf", primary_pdf(), reference.id)

    db.rollback()
    after = {m: count(db, m) for m in (Document, DocumentBlob, Job, JobStage)}
    assert after == before


def test_duplicate_upload_within_window_returns_existing_job(user_a, reference, db):
    first = upload(user_a, reference).json()
    second = upload(user_a, reference)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["job"]["id"] == first["job"]["id"]
    assert count(db, Job) == 1


def test_duplicate_upload_after_window_creates_a_new_job(user_a, reference, db):
    first = upload(user_a, reference).json()
    db.execute(text("UPDATE jobs SET created_at = now() - interval '1 hour'"))
    db.commit()
    second = upload(user_a, reference).json()
    assert second["duplicate"] is False and second["job"]["id"] != first["job"]["id"]


def test_same_file_from_another_user_is_not_a_duplicate(user_a, user_b, reference):
    a = upload(user_a, reference).json()
    b = upload(user_b, reference).json()
    assert b["duplicate"] is False and a["job"]["id"] != b["job"]["id"]


@pytest.mark.parametrize("payload", [b"", b"just text", b"PK\x03\x04 not a real docx"])
def test_invalid_files_are_rejected(user_a, reference, payload):
    assert upload(user_a, reference, data=payload, name="x.docx").status_code in (400, 415)


def test_oversized_upload_is_rejected(user_a, reference, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    assert upload(user_a, reference).status_code == 413


def test_unknown_reference_is_rejected(user_a, reference):
    class R:
        id = "00000000-0000-0000-0000-000000000000"

    assert upload(user_a, R).status_code == 404


# ---- authentication and authorisation -----------------------------------------


def test_endpoints_require_authentication(client, reference):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/references").status_code == 401
    assert client.get(f"/api/references/{reference.id}/file").status_code == 401
    assert client.post("/api/jobs", data={"reference_id": str(reference.id)}).status_code in (401, 422)


def test_user_cannot_read_another_users_job_through_any_endpoint(user_a, user_b, reference):
    job_id = upload(user_a, reference).json()["job"]["id"]
    for path in ("", "/result", "/document", "/events"):
        assert user_b.get(f"/api/jobs/{job_id}{path}").status_code == 404, path
    assert user_b.post(f"/api/jobs/{job_id}/retry").status_code == 404
    assert user_b.get("/api/jobs").json() == []
    assert len(user_a.get("/api/jobs").json()) == 1


def test_owner_can_read_own_job(user_a, reference):
    job_id = upload(user_a, reference).json()["job"]["id"]
    assert user_a.get(f"/api/jobs/{job_id}").status_code == 200
    assert user_a.get(f"/api/jobs/{job_id}/result").status_code == 200


def test_login_flow_and_wrong_password(client):
    assert client.post("/api/auth/signup", json={"email": "Case@Example.com", "password": "password-123"}).status_code == 201
    assert client.post("/api/auth/signup", json={"email": "case@example.com", "password": "password-123"}).status_code == 409
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "case@example.com", "password": "wrong-password"}).status_code == 401
    ok = client.post("/api/auth/login", json={"email": "CASE@example.com", "password": "password-123"})
    assert ok.status_code == 200 and client.get("/api/auth/me").json()["email"] == "case@example.com"


def test_tampered_token_is_rejected(client):
    client.cookies.set("dv_session", "not.a.jwt")
    assert client.get("/api/auth/me").status_code == 401
