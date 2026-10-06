import io
import json

import pytest
from fastapi.testclient import TestClient

from tests.doc_factory import make_docx, make_regulation_pdf
from tests.helpers import seed_minimal


@pytest.fixture()
def client(isolated_env):
    from app.api import deps
    deps.reset_container()
    seed_minimal()
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c
    deps.reset_container()


META = {"doc_id": "SUP-DOC", "title": "Supplementary Notice", "issuer": "Controller of Examinations", "authority_level": 2, "doc_type": "notice",
        "version": "1.0", "effective_from": "2026-01-01", "retrieved_on": "2026-10-05", "synthetic": "Y"}


def test_ask_contract_and_header_identity(client):
    r = client.post("/ask", json={"question": "Am I eligible to appear in the end-semester exam for CS201?", "as_of_date": "2026-10-06"},
                    headers={"X-Student-Id": "S1001"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer_type"] == "calculated" and body["as_of_date"] == "2026-10-06" and len(body["trace_id"]) == 8


def test_ask_validation_errors(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x", "as_of_date": "06/10/2026"}).status_code == 422
    assert client.post("/ask", json={"question": "x", "as_of_date": "2026-13-45"}).status_code == 422
    assert client.post("/ask", json={"question": "x", "student_id": "S1002"}).status_code == 422  # identity is header-only


def test_body_cannot_override_identity(client):
    r = client.post("/ask", json={"question": "What is my attendance in CS201?"}, headers={"X-Student-Id": "S1002"})
    assert "33 of 40" in r.json()["answer"]


def test_ingest_makes_document_searchable_immediately(client):
    r = client.post("/ingest", files={"file": ("n.docx", make_docx(), "application/octet-stream")}, data={"metadata": json.dumps(META)})
    assert r.status_code == 200 and r.json()["doc_id"] == "SUP-DOC" and r.json()["chunks_indexed"] > 0 and r.json()["status"] in ("success", "partial")
    a = client.post("/ask", json={"question": "What is the supplementary fee per course?", "as_of_date": "2026-10-06"}).json()
    assert a["answer_type"] == "retrieved_fact" and a["citations"][0]["doc_id"] == "SUP-DOC"


def test_ingest_rejects_bad_metadata_and_bad_files(client):
    bad = dict(META, authority_level=9)
    r = client.post("/ingest", files={"file": ("n.docx", make_docx(), "x")}, data={"metadata": json.dumps(bad)})
    assert r.status_code == 422
    r = client.post("/ingest", files={"file": ("n.pdf", b"not a pdf", "x")}, data={"metadata": json.dumps(META)})
    assert r.status_code == 422
    r = client.post("/ingest", files={"file": ("n.docx", make_docx(), "x")}, data={"metadata": "{broken"})
    assert r.status_code == 422


def test_health_sources_and_audit_access_control(client):
    h = client.get("/health").json()
    assert h["sqlite"] == "ok" and h["llm"] == "mock"
    assert any(s["doc_id"] == "ACAD-REG-2024" for s in client.get("/sources").json())
    tid = client.post("/ask", json={"question": "What is my attendance in CS201?"}, headers={"X-Student-Id": "S1001"}).json()["trace_id"]
    assert client.get(f"/audit/{tid}").status_code == 403
    assert client.get(f"/audit/{tid}", headers={"X-Student-Id": "S1002"}).status_code == 403
    ok = client.get(f"/audit/{tid}", headers={"X-Student-Id": "S1001"})
    assert ok.status_code == 200 and ok.json()["trace_id"] == tid
    assert client.get("/audit/deadbeef").status_code == 404


def test_admin_loader_reports_bad_rows(client):
    students = "student_id,full_name,programme,batch_year,current_semester,cgpa,active_backlogs\nS2001,Test One,B.Tech CSE,2024,3,8.1,0\nBAD,X,B.Tech CSE,2024,3,8.1,0\n"
    r = client.post("/admin/load", files={"students": ("s.csv", students, "text/csv")})
    rep = r.json()["reports"][0]
    assert rep["rows_loaded"] == 1 and rep["errors"][0]["line"] == 3
    a = client.post("/ask", json={"question": "What is my CGPA?"}, headers={"X-Student-Id": "S2001"}).json()
    assert "8.10" in a["answer"]


def test_admin_token_protects_ingest_when_configured(isolated_env, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    from app.core import config
    config.reset_settings_cache()
    from app.api import deps
    deps.reset_container()
    seed_minimal()
    from app.main import create_app
    with TestClient(create_app()) as c:
        r = c.post("/ingest", files={"file": ("n.docx", make_docx(), "x")}, data={"metadata": json.dumps(META)})
        assert r.status_code == 401
        r = c.post("/ingest", files={"file": ("n.docx", make_docx(), "x")}, data={"metadata": json.dumps(META)}, headers={"X-Admin-Token": "secret"})
        assert r.status_code == 200
    deps.reset_container()
