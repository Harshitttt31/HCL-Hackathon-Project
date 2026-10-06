from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

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


def login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_student_first_login_creates_account_and_token_identifies_student(client):
    r = login(client, "s1001", "student123")  # student ids are case-insensitive
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 3600
    assert body["user"]["student_id"] == "S1001" and body["user"]["role"] == "student"
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["sub"] == "S1001" and claims["sid"] == "S1001" and "password" not in str(claims)
    me = client.get("/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200 and me.json()["student_id"] == "S1001"


def test_wrong_password_unknown_user_and_unknown_student_are_rejected_alike(client):
    for user, pw in (("S1001", "nope"), ("S9999", "student123"), ("nobody", "x")):
        r = login(client, user, pw)
        assert r.status_code == 401 and r.json()["detail"] == "incorrect username or password"


def test_ask_uses_token_identity(client):
    token = login(client, "S1002", "student123").json()["access_token"]
    r = client.post("/ask", json={"question": "What is my attendance in CS201?"}, headers=bearer(token))
    assert r.status_code == 200 and "33 of 40" in r.json()["answer"]


def test_header_that_contradicts_token_is_refused(client):
    token = login(client, "S1002", "student123").json()["access_token"]
    r = client.post("/ask", json={"question": "What is my attendance in CS201?"}, headers={**bearer(token), "X-Student-Id": "S1001"})
    assert r.status_code == 403


def test_bad_and_expired_tokens_are_401(client):
    assert client.get("/auth/me", headers=bearer("not-a-token")).status_code == 401
    expired = jwt.encode({"sub": "S1001", "role": "student", "sid": "S1001", "iss": "univ-assistant",
                          "iat": datetime.now(timezone.utc) - timedelta(hours=2), "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
                         "test-jwt-secret-that-is-long-enough-for-hs256", algorithm="HS256")
    r = client.post("/ask", json={"question": "What is my attendance?"}, headers=bearer(expired))
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    forged = jwt.encode({"sub": "S1001", "role": "admin", "iss": "univ-assistant", "iat": datetime.now(timezone.utc),
                         "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "some-other-secret-that-is-also-long-enough", algorithm="HS256")
    assert client.get("/auth/me", headers=bearer(forged)).status_code == 401


def test_x_student_id_still_works_unless_auth_required(client, monkeypatch):
    q = {"question": "What is my attendance in CS201?"}
    assert client.post("/ask", json=q, headers={"X-Student-Id": "S1002"}).status_code == 200
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    from app.core import config
    config.reset_settings_cache()
    assert client.post("/ask", json=q, headers={"X-Student-Id": "S1002"}).status_code == 401
    token = login(client, "S1002", "student123").json()["access_token"]
    assert client.post("/ask", json=q, headers=bearer(token)).status_code == 200
    # anonymous policy questions stay open
    assert client.post("/ask", json={"question": "What is the minimum attendance?"}).status_code == 200


def test_audit_is_readable_by_owner_token_and_admin_only(client):
    s1 = login(client, "S1001", "student123").json()["access_token"]
    s2 = login(client, "S1002", "student123").json()["access_token"]
    admin = login(client, "admin", "admin-pass-123").json()["access_token"]
    tid = client.post("/ask", json={"question": "What is my attendance in CS201?"}, headers=bearer(s1)).json()["trace_id"]
    assert client.get(f"/audit/{tid}", headers=bearer(s1)).status_code == 200
    assert client.get(f"/audit/{tid}", headers=bearer(s2)).status_code == 403
    assert client.get(f"/audit/{tid}", headers=bearer(admin)).status_code == 200


def test_admin_endpoints_need_admin_role_when_a_token_is_sent(client):
    student = login(client, "S1001", "student123").json()["access_token"]
    admin = login(client, "admin", "admin-pass-123").json()["access_token"]
    files = {"students": ("s.csv", "student_id,full_name,programme,batch_year,current_semester,cgpa,active_backlogs\nS2001,Test One,B.Tech CSE,2024,3,8.1,0\n", "text/csv")}
    assert client.post("/admin/load", files=files, headers=bearer(student)).status_code == 403
    assert client.post("/admin/load", files=files, headers=bearer(admin)).status_code == 200
    # an admin is not a student: personal questions get no student identity
    assert client.get("/me/overview", headers=bearer(admin)).status_code == 403


def test_change_password(client):
    token = login(client, "S1001", "student123").json()["access_token"]
    h = bearer(token)
    assert client.post("/auth/change-password", json={"current_password": "wrong", "new_password": "longenough1"}, headers=h).status_code == 400
    assert client.post("/auth/change-password", json={"current_password": "student123", "new_password": "short"}, headers=h).status_code == 400
    assert client.post("/auth/change-password", json={"current_password": "student123", "new_password": "longenough1"}, headers=h).status_code == 204
    assert login(client, "S1001", "student123").status_code == 401
    assert login(client, "S1001", "longenough1").status_code == 200


def test_repeated_failures_lock_the_account(client):
    for _ in range(5):
        assert login(client, "S1001", "wrong").status_code == 401
    r = login(client, "S1001", "student123")
    assert r.status_code == 429


def test_overview_returns_own_records(client):
    token = login(client, "S1002", "student123").json()["access_token"]
    o = client.get("/me/overview", headers=bearer(token)).json()
    assert o["profile"]["student_id"] == "S1002"
    lines = {c["course_code"]: c for c in o["attendance"]["courses"]}
    assert lines["CS201"]["classes_attended"] == 33 and lines["CS201"]["classes_held"] == 40


def test_web_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "app.js" in r.text and "Content-Security-Policy" in r.headers
    assert client.get("/static/app.js").status_code == 200
