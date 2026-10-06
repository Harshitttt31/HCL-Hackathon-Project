from __future__ import annotations

from typing import Optional

from app.audit.models import AuditRecord
from app.core.logging import get_logger
from app.database.repositories import AuditRepo, StudentRepo

log = get_logger(__name__)


class AuditLogger:
    def __init__(self, repo: Optional[AuditRepo] = None):
        self.repo = repo or AuditRepo()

    def write(self, rec: AuditRecord) -> bool:
        """Persist the record. A failure is logged (without personal data) and never breaks the user's answer."""
        try:
            self.repo.insert(rec.trace_id, rec.ts, rec.student_id_hash, rec.question_hash, rec.question_category,
                             rec.answer_type, rec.latency_ms, rec.model_dump(mode="json"))
            log.info("audit_written", extra={"trace_id": rec.trace_id, "answer_type": rec.answer_type, "latency_ms": rec.latency_ms})
            return True
        except Exception as exc:  # pragma: no cover - defensive
            log.error("audit_write_failed", extra={"trace_id": rec.trace_id, "error": type(exc).__name__})
            return False

    def read(self, trace_id: str) -> Optional[dict]:
        return self.repo.get(trace_id)
