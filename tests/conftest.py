"""Shared fixtures. Every test runs against an isolated temp SQLite file, temp Chroma dir, and the mock LLM."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """Point the app at throw-away storage and force deterministic backends."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("EMBEDDING_BACKEND", "hashing")
    monkeypatch.setenv("AUDIT_SALT", "test-salt")
    monkeypatch.setenv("AS_OF_DATE_OVERRIDE", "")
    # pin auth settings so a developer's .env cannot change test behaviour
    monkeypatch.setenv("ADMIN_TOKEN", "")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-that-is-long-enough-for-hs256")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    monkeypatch.setenv("DEMO_STUDENT_PASSWORD", "student123")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "5")
    from app.auth import service as auth_service
    auth_service.lockout._state.clear()
    from app.core import config
    config.reset_settings_cache()
    from app.database import sqlite
    sqlite._initialised.clear()
    # drop module-level singletons so each test builds fresh objects
    import app.sources.registry as registry
    registry._default = None
    from app.rag.embeddings import reset_embedder
    reset_embedder()
    yield tmp_path
    config.reset_settings_cache()
