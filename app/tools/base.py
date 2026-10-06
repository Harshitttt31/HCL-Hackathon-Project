"""Tool framework.

Every tool has: a Pydantic input schema, a Pydantic output schema, validation, error handling and
audit logging (via the ToolCall record). The LLM may SELECT a tool; it can never override its result.

Safety properties enforced here:
* input models forbid extra keys, and none of them has a student_id field: the student comes only from
  ToolContext (the X-Student-Id header), so neither the LLM nor the question text can redirect a lookup.
* tools never raise to the caller: failures become ToolCall(status="error"|"not_found"|"denied").
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import RecordNotFound, ToolError
from app.core.logging import get_logger
from app.database.models import StudentRow
from app.database.repositories import (
    AttendanceRepo,
    CourseRepo,
    ResultRepo,
    RuleRepo,
    StudentRepo,
)
from app.sources.models import DocInfo
from app.sources.registry import SourceRegister, get_register

log = get_logger(__name__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCall(BaseModel):
    """One audited tool invocation. `input`/`output` are exactly what the API returns in tools_invoked."""

    tool: str
    input: dict[str, Any]
    output: Optional[dict[str, Any]] = None
    status: str = "ok"  # ok | error | not_found | denied
    error: Optional[str] = None
    ms: int = 0
    personal: bool = False


@dataclass
class Services:
    students: StudentRepo = field(default_factory=StudentRepo)
    courses: CourseRepo = field(default_factory=CourseRepo)
    attendance: AttendanceRepo = field(default_factory=AttendanceRepo)
    results: ResultRepo = field(default_factory=ResultRepo)
    rules: RuleRepo = field(default_factory=RuleRepo)
    register: SourceRegister = field(default_factory=get_register)
    retriever: Any = None  # set by the application container (HybridRetriever)


@dataclass
class ToolContext:
    student_id: Optional[str]
    as_of: date
    request_id: str = ""
    programme_hint: Optional[str] = None  # from the question text, anonymous policy questions only
    batch_hint: Optional[int] = None
    services: Services = field(default_factory=Services)
    _student: Optional[StudentRow] = field(default=None, repr=False)
    _student_loaded: bool = field(default=False, repr=False)
    _docs: Optional[dict[str, DocInfo]] = field(default=None, repr=False)

    def student(self) -> Optional[StudentRow]:
        if not self._student_loaded:
            self._student = self.services.students.get(self.student_id) if self.student_id else None
            self._student_loaded = True
        return self._student

    def require_student(self) -> StudentRow:
        if not self.student_id:
            raise PermissionError("no student identity in the request context")
        student = self.student()
        if student is None:
            raise RecordNotFound("student record for the current identity")
        return student

    def scope(self) -> tuple[Optional[str], Optional[int]]:
        """Programme and batch used to decide which documents/rules apply."""
        student = self.student()
        if student:
            return student.programme, student.batch_year
        return self.programme_hint, self.batch_hint

    def docs(self) -> dict[str, DocInfo]:
        if self._docs is None:
            self._docs = self.services.register.infos()
        return self._docs


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], BaseModel]
    personal: bool = False
    aliases: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec
        for alias in spec.aliases:
            self._tools[alias] = spec

    def names(self) -> list[str]:
        return sorted({s.name for s in self._tools.values()})

    def spec(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "description": s.description, "input_schema": s.input_model.model_json_schema(), "personal": s.personal}
            for s in sorted({id(s): s for s in self._tools.values()}.values(), key=lambda x: x.name)
        ]

    def call(self, name: str, ctx: ToolContext, args: Optional[dict[str, Any]] = None) -> ToolCall:
        """Validate, execute, validate output, time it. Never raises."""
        args = dict(args or {})
        spec = self._tools.get(name)
        started = time.perf_counter()
        if spec is None:
            return ToolCall(tool=name, input=args, status="error", error=f"unknown tool '{name}'")
        try:
            parsed = spec.input_model(**args)
        except ValidationError as exc:
            return ToolCall(tool=spec.name, input=args, status="error", personal=spec.personal,
                            error="invalid input: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
                            ms=int((time.perf_counter() - started) * 1000))
        status, error, output = "ok", None, None
        try:
            result = spec.fn(ctx, parsed)
            validated = spec.output_model.model_validate(result.model_dump() if isinstance(result, BaseModel) else result)
            output = validated.model_dump(mode="json")
        except RecordNotFound as exc:
            status, error = "not_found", f"{exc.what} not found"
        except PermissionError as exc:
            status, error = "denied", str(exc)
        except (ToolError, ValueError, ValidationError) as exc:
            status, error = "error", str(exc)
        except Exception as exc:  # defensive: a tool bug must never crash a request
            log.exception("tool_failure", extra={"tool": spec.name, "request_id": ctx.request_id})
            status, error = "error", f"internal error ({type(exc).__name__})"
        ms = int((time.perf_counter() - started) * 1000)
        log.info("tool_call", extra={"tool": spec.name, "status": status, "ms": ms, "request_id": ctx.request_id})
        return ToolCall(tool=spec.name, input=parsed.model_dump(mode="json"), output=output, status=status, error=error,
                        ms=ms, personal=spec.personal)
