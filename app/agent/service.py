"""AssistantService: the single entry point used by the API, the evaluation harness and the scripts."""
from __future__ import annotations

import time
import uuid
from datetime import date
from typing import Any, Optional

from app.agent.graph import build_graph
from app.agent.llm import get_llm
from app.agent.nodes import Deps, contract_citation, contract_rule
from app.audit.logger import AuditLogger
from app.core.clock import parse_iso_date, today
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.database.sqlite import init_db
from app.rag.retriever import HybridRetriever
from app.tools.base import Services
from app.tools.registry import build_registry

log = get_logger(__name__)


class AssistantService:
    def __init__(self, retriever: Optional[HybridRetriever] = None):
        init_db()
        self.retriever = retriever or HybridRetriever()
        self.registry = build_registry()
        services = Services()
        services.retriever = self.retriever
        self.deps = Deps(registry=self.registry, retriever=self.retriever, llm=get_llm(), audit=AuditLogger(), services=services)
        self.graph = build_graph(self.deps)

    def ask(self, question: str, student_id: Optional[str] = None, as_of_date: Optional[str] = None) -> dict[str, Any]:
        """Returns the contract response. Raises ValidationFailed only for malformed input (blank question, bad date)."""
        if question is None or not str(question).strip():
            raise ValidationFailed("question must not be empty")
        try:
            as_of = parse_iso_date(as_of_date) or today()
        except ValueError as exc:
            raise ValidationFailed("as_of_date must be in YYYY-MM-DD format") from exc
        trace_id = uuid.uuid4().hex[:8]
        state = {"trace_id": trace_id, "question": str(question), "student_id": student_id, "as_of": as_of,
                 "started": time.perf_counter(), "node_trace": [], "warnings": []}
        final = self.graph.invoke(state)
        calls = final.get("tool_calls", [])
        return {
            "trace_id": trace_id,
            "answer": final["answer"],
            "answer_type": final["answer_type"],
            "citations": [contract_citation(c) for c in final["citations"] if c.get("role") not in ("superseded", "overridden")],
            "tools_invoked": [{"tool": c.tool, "input": c.input, "output": c.output if c.status == "ok" else {"status": c.status, "error": c.error}}
                              for c in calls],
            "applied_rules": [contract_rule(r) for r in final["applied_rules"]],
            "conflicts_detected": final["conflicts_detected"],
            "explanation": final["explanation"],
            "as_of_date": as_of.isoformat(),
        }

    def reload_index(self) -> int:
        return self.retriever.reload()
