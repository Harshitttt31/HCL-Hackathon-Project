from __future__ import annotations

import threading
from typing import Optional

from fastapi import Header, HTTPException

from app.agent.service import AssistantService
from app.core.config import get_settings
from app.core.security import constant_time_equals
from app.rag.ingestion import IngestionService

_lock = threading.Lock()
_service: Optional[AssistantService] = None
_ingestion: Optional[IngestionService] = None


def get_service() -> AssistantService:
    global _service, _ingestion
    with _lock:
        if _service is None:
            _service = AssistantService()
            _ingestion = IngestionService(_service.retriever)
        return _service


def get_ingestion() -> IngestionService:
    get_service()
    assert _ingestion is not None
    return _ingestion


def reset_container() -> None:
    global _service, _ingestion
    with _lock:
        _service = None
        _ingestion = None


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    """Admin endpoints are open unless ADMIN_TOKEN is configured (hackathon default); then the token is mandatory."""
    token = get_settings().admin_token
    if token and not constant_time_equals(x_admin_token, token):
        raise HTTPException(status_code=401, detail="admin token required")
