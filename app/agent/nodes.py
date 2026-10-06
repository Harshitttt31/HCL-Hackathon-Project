"""LangGraph nodes. Each node reads and writes named state fields and appends one entry to the node trace.

Node order (see graph.py):
 input_guard -> classify -> privacy_check -> (refuse | plan) -> execute_tools -> (document_path)? -> compose
 -> generate -> validate -> finalize
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.agent import handlers
from app.agent.classifier import classify
from app.agent.generator import llm_intent, polish_answer
from app.agent.llm import LLMClient
from app.agent.policy import answer_from_documents
from app.agent.privacy import check_privacy
from app.agent.state import PERSONAL_INTENTS, AgentState, Classification, Finding, PrivacyVerdict
from app.agent.validator import validate_answer
from app.audit.logger import AuditLogger
from app.audit.models import AuditRecord
from app.core.clock import now_iso
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import hash_question, hash_student_id, normalize_student_id, redact_text
from app.database.repositories import ChunkRepo, CourseRepo, StudentRepo
from app.rag.retriever import HybridRetriever
from app.tools.base import Services, ToolContext, ToolRegistry

log = get_logger(__name__)

PRIORITY = ["refused", "conflict_flagged", "clarification_needed", "calculated", "retrieved_fact", "not_found"]
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class Deps:
    registry: ToolRegistry
    retriever: HybridRetriever
    llm: LLMClient
    audit: AuditLogger
    services: Services


def traced(name: str) -> Callable:
    """Wrap a node so its duration and a one-line summary land in the node trace."""

    def deco(fn: Callable[[Deps, AgentState], dict[str, Any]]) -> Callable[[Deps, AgentState], dict[str, Any]]:
        def wrapper(deps: Deps, state: AgentState) -> dict[str, Any]:
            t0 = time.perf_counter()
            update = fn(deps, state)
            summary = update.pop("_summary", "")
            trace = list(state.get("node_trace", []))
            trace.append({"node": name, "ms": round((time.perf_counter() - t0) * 1000, 1), "summary": summary})
            update["node_trace"] = trace
            return update

        wrapper.__name__ = fn.__name__
        return wrapper

    return deco


# ------------------------------------------------------------------------------------------------
@traced("input_guard")
def input_guard(deps: Deps, state: AgentState) -> dict[str, Any]:
    q = CONTROL_RE.sub(" ", state.get("question", ""))
    q = re.sub(r"\s+", " ", q).strip()
    max_chars = get_settings().max_question_chars
    warnings = list(state.get("warnings", []))
    squeezed = re.sub(r"(\S)\1{7,}", r"\1\1\1", q)  # a run of one repeated character carries no information
    if squeezed != q:
        q = squeezed
        warnings.append("long runs of a repeated character were shortened")
    if len(q) > max_chars:
        q = q[:max_chars]
        warnings.append(f"question truncated to {max_chars} characters")
    return {"cleaned_question": q, "warnings": warnings, "_summary": f"{len(q)} characters"}


@traced("classify")
def classify_node(deps: Deps, state: AgentState) -> dict[str, Any]:
    q = state["cleaned_question"]
    names = {c.course_name.lower(): c.course_code for c in CourseRepo().list_for_programme()}
    cls = classify(q, names)
    s = get_settings()
    if cls.intents == ["policy_text"] and s.llm_assist_planning and not deps.llm.is_mock:
        guess = llm_intent(deps.llm, q)
        if guess and guess[1] >= 0.6 and guess[0] != "policy_text":
            cls.intents, cls.confidence, cls.source = [guess[0]], guess[1], "llm"
            cls.notes.append(f"LLM tie-break chose {guess[0]} (confidence {guess[1]:.2f})")
    return {"classification": cls, "_summary": f"intents={cls.intents} source={cls.source} courses={cls.slots.course_codes}"}


@traced("privacy_check")
def privacy_node(deps: Deps, state: AgentState) -> dict[str, Any]:
    cls: Classification = state["classification"]
    own = normalize_student_id(state.get("student_id"))
    verdict = check_privacy(state["cleaned_question"], state.get("student_id"), cls, bool(own))
    if verdict.allowed and own and cls.personal and StudentRepo().get(own) is None:
        verdict = PrivacyVerdict(False, "I could not find a student record for the identity supplied, so I can't answer questions about personal records.",
                                 "unknown_identity", "header identity has no student record")
    if verdict.allowed and cls.injection_flag and cls.intents == ["policy_text"]:
        verdict = PrivacyVerdict(False, "I can't follow instructions embedded in a question. I can answer questions about university policy and about your own records.",
                                 "prompt_injection", "instruction-like text with no legitimate question")
    return {"privacy": verdict, "_summary": "allowed" if verdict.allowed else f"refused ({verdict.code})"}


@traced("refuse")
def refuse_node(deps: Deps, state: AgentState) -> dict[str, Any]:
    v: PrivacyVerdict = state["privacy"]
    return {"findings": [Finding("refusal", "refused", v.reason)], "_summary": v.code}


@traced("plan")
def plan_node(deps: Deps, state: AgentState) -> dict[str, Any]:
    cls: Classification = state["classification"]
    plan: list[dict[str, Any]] = []
    for intent in cls.intents:
        if intent == "policy_text":
            plan.append({"intent": intent, "tools": ["search_documents"], "path": "documents"})
        else:
            plan.append({"intent": intent, "tools": _tools_for(intent), "path": "tools"})
    return {"plan": plan, "_summary": " | ".join(f"{p['intent']}->{','.join(p['tools'])}" for p in plan)}


def _tools_for(intent: str) -> list[str]:
    return {
        "attendance_status": ["get_attendance"],
        "attendance_projection": ["get_rule", "calculate_attendance_projection"],
        "attendance_what_if": ["check_attendance_band"],
        "exam_eligibility": ["check_exam_eligibility"],
        "supplementary_eligibility": ["check_exam_eligibility"],
        "placement_eligibility": ["check_placement_eligibility"],
        "cgpa": ["calculate_cgpa"],
        "results_status": ["get_results"],
        "student_profile": ["get_student_profile"],
        "policy_parameter": ["get_rule"],
    }.get(intent, [])


def _tool_context(deps: Deps, state: AgentState) -> ToolContext:
    cls: Classification = state["classification"]
    own = normalize_student_id(state.get("student_id"))
    return ToolContext(student_id=own, as_of=state["as_of"], request_id=state["trace_id"], programme_hint=cls.slots.programme,
                       batch_hint=cls.slots.batch_year, services=deps.services)


@traced("execute_tools")
def execute_tools(deps: Deps, state: AgentState) -> dict[str, Any]:
    cls: Classification = state["classification"]
    ctx = _tool_context(deps, state)
    runner = handlers.Runner(deps.registry, ctx)
    findings: list[Finding] = []
    needs_documents = False
    for step in state["plan"]:
        intent = step["intent"]
        if intent == "policy_text":
            needs_documents = True
            continue
        fn = handlers.HANDLERS[intent]
        got = fn(runner, cls)
        if intent == "policy_parameter" and not got:
            needs_documents = True  # nothing in the rule registry for this parameter: fall back to the document text
        findings.extend(got)
    return {"findings": findings, "tool_calls": runner.calls, "plan": [dict(p, needs_documents=needs_documents) for p in state["plan"]],
            "_summary": f"{len(runner.calls)} tool call(s); statuses={[c.status for c in runner.calls]}; documents_needed={needs_documents}"}


def needs_documents(state: AgentState) -> bool:
    return any(p.get("needs_documents") for p in state.get("plan", []))


@traced("document_path")
def document_path(deps: Deps, state: AgentState) -> dict[str, Any]:
    cls: Classification = state["classification"]
    ctx = _tool_context(deps, state)
    prog, batch = ctx.scope()
    out = answer_from_documents(deps.retriever, state["cleaned_question"], state["as_of"], prog, batch, ctx.docs())
    findings = list(state.get("findings", [])) + out.findings
    return {"findings": findings, "evidence": out.evidence, "retrieval_stats": out.stats, "coverage": out.coverage,
            "missing_terms": out.missing_terms,
            "warnings": list(state.get("warnings", [])) + ([f"{out.injection_dropped} retrieved chunk(s) excluded: instruction-like text"] if out.injection_dropped else []),
            "_summary": "; ".join(out.trace[-4:])[:300]}


@traced("compose")
def compose(deps: Deps, state: AgentState) -> dict[str, Any]:
    findings: list[Finding] = state.get("findings", [])
    if not findings:
        findings = [Finding("not_found", "not_found", "I could not find this in the authorised university documents I have, so I won't guess.")]
    types = {f.answer_type for f in findings}
    answer_type = next(t for t in PRIORITY if t in types)
    # a not_found part next to a real answer is informative, but a refusal/unknown-record part must not bury it
    texts: list[str] = []
    for f in findings:
        if f.text and f.text not in texts:
            if answer_type in ("calculated", "retrieved_fact") and f.answer_type == "not_found" and len(findings) > 1 and f.kind == "not_found" and "could not find" not in f.text:
                continue
            texts.append(f.text)
    draft = "\n\n".join(texts)
    citations, rules, conflicts, explanation, facts = [], [], [], [], []
    seen_c, seen_r, seen_x = set(), set(), set()
    for f in findings:
        for c in f.citations:
            k = (c["doc_id"], c["section"], c.get("role"))
            if k not in seen_c:
                seen_c.add(k)
                citations.append(c)
        for r in f.rules:
            if r["rule_id"] not in seen_r:
                seen_r.add(r["rule_id"])
                rules.append(r)
        for x in f.conflicts:
            k = (x.get("topic"), x.get("kind"), x.get("explanation"))
            if k not in seen_x:
                seen_x.add(k)
                conflicts.append(x)
        if f.explanation:
            explanation.append(f.explanation)
        facts.extend(f.facts)
    for x in conflicts:
        if x.get("explanation") and x["explanation"] not in explanation:
            explanation.append(x["explanation"])
    expl = " ".join(explanation) or ("No calculation was needed." if answer_type != "refused" else "The request was refused by the privacy policy.")
    if answer_type in ("calculated", "retrieved_fact"):
        expl += f" Rules and documents are applied as in force on {state['as_of'].isoformat()}."
    return {"answer_type": answer_type, "draft_answer": draft, "answer": draft, "explanation": expl.strip(), "citations": citations,
            "applied_rules": rules, "conflicts_detected": conflicts, "allowed_facts": sorted(set(facts)),
            "_summary": f"type={answer_type} citations={len(citations)} rules={len(rules)} conflicts={len(conflicts)}"}


@traced("generate")
def generate(deps: Deps, state: AgentState) -> dict[str, Any]:
    s = get_settings()
    if state["answer_type"] in ("refused",) or not s.llm_polish:
        return {"llm_used": False, "validation": {"ok": True, "issues": [], "skipped": "no LLM rewording for this answer"}, "_summary": "template answer"}
    text, used, val = polish_answer(deps.llm, state["cleaned_question"], state["draft_answer"], state.get("allowed_facts", []))
    validation = val.as_dict() if val is not None else {"ok": True, "issues": [], "skipped": "LLM returned nothing usable; template answer used"}
    if val is not None and not val.ok:
        validation["fallback"] = "LLM wording rejected; deterministic answer used"
    return {"answer": text, "llm_used": used, "validation": validation,
            "_summary": f"llm_text_used={used} validation_ok={validation.get('ok')}"}


@traced("validate")
def validate(deps: Deps, state: AgentState) -> dict[str, Any]:
    """Final gate on whatever text will be returned (also covers the template answer)."""
    res = validate_answer(state["answer"], state["draft_answer"], state.get("allowed_facts", []), state["cleaned_question"])
    answer = state["answer"]
    validation = dict(state.get("validation", {}))
    if not res.ok:
        answer = state["draft_answer"]
        validation.update({"ok": False, "final_check_issues": res.issues, "fallback": "deterministic answer used"})
    else:
        validation["final_check"] = "passed"
    return {"answer": answer, "validation": validation, "_summary": "passed" if res.ok else f"rejected: {res.issues[:2]}"}


def contract_citation(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in ("doc_id", "title", "section", "page", "version", "effective_from")}


def contract_rule(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r.get(k) for k in ("rule_id", "value", "source_doc_id")}


@traced("finalize")
def finalize(deps: Deps, state: AgentState) -> dict[str, Any]:
    cls: Classification = state["classification"]
    latency = int((time.perf_counter() - state["started"]) * 1000)
    calls = state.get("tool_calls", [])
    res = state.get("coverage")
    excluded = []
    rec = AuditRecord(
        trace_id=state["trace_id"], ts=now_iso(), as_of_date=state["as_of"].isoformat(),
        student_id_hash=hash_student_id(normalize_student_id(state.get("student_id"))),
        question_hash=hash_question(state["cleaned_question"]), question_redacted=redact_text(state["cleaned_question"])[:600],
        question_category=(cls.intents[0] if cls.intents else ""), intents=cls.intents, classification_source=cls.source,
        privacy={"allowed": state["privacy"].allowed, "code": state["privacy"].code, "detail": state["privacy"].detail},
        answer_type=state["answer_type"], answer=state["answer"], explanation=state["explanation"],
        node_trace=state.get("node_trace", []),
        tools_invoked=[{"tool": c.tool, "input": c.input, "output": c.output, "status": c.status, "error": c.error, "ms": c.ms} for c in calls],
        applied_rules=state["applied_rules"], citations=state["citations"], conflicts_detected=state["conflicts_detected"],
        excluded_sources=excluded, retrieval={**(state.get("retrieval_stats") or {}), "coverage": res, "missing_terms": state.get("missing_terms", [])},
        evidence_chunk_ids=[e.chunk_id for e in state.get("evidence", [])][:20], validation=state.get("validation", {}),
        llm={"backend": deps.llm.name, "model": deps.llm.model, "used_for_wording": bool(state.get("llm_used"))},
        warnings=state.get("warnings", []), prompt_version=get_settings().prompt_version, latency_ms=latency)
    deps.audit.write(rec)
    return {"_summary": f"audit written, {latency} ms"}
