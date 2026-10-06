"""Intent handlers: call allowlisted tools and turn their structured output into Findings.

Every sentence produced here is built from tool output (numbers, rule ids, clauses). The LLM is not involved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.agent.state import Classification, Finding
from app.database.repositories import ChunkRepo
from app.rag.claims import get_lexicon
from app.sources.citations import citation_for_clause
from app.tools.base import ToolCall, ToolContext, ToolRegistry
from app.tools.rules import RuleInfo, RuleResolution

OP_WORDS = {">=": "at least ", "<=": "at most ", ">": "more than ", "<": "less than ", "==": "", "=": "", "!=": "not "}
UNIT_SUFFIX = {"pct": "%", "cgpa": "", "count": "", "grade_points": ""}


@dataclass
class Runner:
    registry: ToolRegistry
    ctx: ToolContext
    chunks: ChunkRepo = field(default_factory=ChunkRepo)
    calls: list[ToolCall] = field(default_factory=list)

    def call(self, name: str, args: Optional[dict[str, Any]] = None) -> ToolCall:
        tc = self.registry.call(name, self.ctx, args or {})
        self.calls.append(tc)
        return tc

    def cite_rule(self, rule: RuleInfo, role: str = "supports") -> dict[str, Any]:
        doc = self.ctx.docs().get(rule.source_doc_id)
        return citation_for_clause(self.chunks, doc, rule.source_doc_id, rule.source_section, rule.source_page, rule.quote, role).model_dump(mode="json")


def rule_payload(rule: RuleInfo) -> dict[str, Any]:
    return {"rule_id": rule.rule_id, "value": rule.display, "source_doc_id": rule.source_doc_id, "section": rule.source_section}


def _dedupe(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for it in items:
        k = tuple(it.get(x) for x in keys)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def human_value(rule: RuleInfo) -> str:
    """'at least 80%' style phrase for a rule value."""
    if rule.operator == "between":
        lo, _, hi = rule.value.partition("-")
        return f"between {lo} and {hi}{UNIT_SUFFIX.get(rule.unit, '')}"
    if rule.operator == "in":
        return rule.value.replace(";", " or ")
    suffix = UNIT_SUFFIX.get(rule.unit, f" {rule.unit}" if rule.unit and rule.unit not in ("date", "currency", "inr") else "")
    prefix = "Rs. " if rule.unit in ("currency", "inr") else ""
    return f"{OP_WORDS.get(rule.operator, '')}{prefix}{rule.value}{suffix}"


def conflict_dicts(conflicts: list[Any]) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json") if hasattr(c, "model_dump") else dict(c) for c in conflicts]


def short_note(c: dict[str, Any]) -> str:
    """One-sentence version of a conflict record for the answer text (the full explanation stays in `explanation`)."""
    kind, w, others = c.get("kind"), c.get("winner"), c.get("overridden") or []
    o = others[0] if others else {}

    def ref(src: dict[str, Any]) -> str:
        sec = f" clause {src['section']}" if src.get("section") else ""
        return f"{src.get('doc_id')}{sec}"

    if kind == "supersession" and w and o:
        return (f"Note: {ref(o)} ({o.get('value')}) was superseded by {w.get('doc_id')} effective {w.get('effective_from')}, "
                f"so {w.get('value')} applies.")
    if kind == "authority" and w and o:
        return (f"Note: {ref(o)} says {o.get('value')} but {ref(w)} has higher authority (level {w.get('authority_level')} "
                f"against level {o.get('authority_level')}), so {w.get('value')} applies.")
    if kind == "recency" and w and o:
        return f"Note: {ref(o)} says {o.get('value')} but the later {ref(w)} (effective {w.get('effective_from')}) prevails."
    return c.get("explanation", "")


def conflict_notes(conflicts: list[dict[str, Any]]) -> str:
    return " ".join(dict.fromkeys(n for n in (short_note(c) for c in conflicts) if n))


def upcoming_notes(upcoming: list[Any]) -> str:
    out = []
    for u in upcoming:
        d = u.model_dump(mode="json") if hasattr(u, "model_dump") else u
        out.append(f"Note: {d.get('doc_id')} {d.get('section') or ''} will change this to {d.get('value')} from {d.get('effective_from')}; "
                   "it is not in force yet and was not used.".replace("  ", " "))
    return " ".join(dict.fromkeys(out))


def facts_from(text: str) -> list[str]:
    from app.agent.validator import extract_tokens
    return sorted(extract_tokens(text))


# ------------------------------------------------------------------------------------------------
# Personal attendance
# ------------------------------------------------------------------------------------------------
def handle_attendance_status(r: Runner, cls: Classification) -> list[Finding]:
    codes = cls.slots.course_codes
    findings: list[Finding] = []
    targets = codes or [None]
    for code in targets:
        tc = r.call("get_attendance", {"course_code": code} if code else {})
        if tc.status != "ok":
            findings.append(_data_missing("attendance", tc, code))
            continue
        o = tc.output or {}
        if code:
            text = (f"Your attendance in {o['course_code']}{' (' + o['course_name'] + ')' if o.get('course_name') else ''} is {o['attendance_pct']:g}%: "
                    f"{o['classes_attended']} of {o['classes_held']} classes attended.")
            findings.append(Finding("attendance", "calculated", text, facts_from(text),
                                    explanation=f"attendance = {o['classes_attended']} / {o['classes_held']} x 100 = {o['attendance_pct']:g}%"))
        else:
            lines = [f"{c['course_code']}: {c['attendance_pct']:g}% ({c['classes_attended']} of {c['classes_held']})" for c in o.get("courses", [])]
            text = "Your attendance by course: " + "; ".join(lines) + f". Overall: {o.get('overall_pct'):g}%."
            findings.append(Finding("attendance", "calculated", text, facts_from(text),
                                    explanation="attendance = classes attended / classes held x 100, computed per course; overall uses the sums of all courses"))
    return findings


def _data_missing(kind: str, tc: ToolCall, code: Optional[str]) -> Finding:
    if tc.status == "denied":
        return Finding("not_found", "refused", "I can't access personal records without a valid student identity.")
    what = f" for {code}" if code else ""
    if tc.status == "not_found":
        text = f"I could not find a {kind} record{what} for you in the university database."
    else:
        text = f"I could not retrieve your {kind}{what} ({tc.error or 'error'})."
    return Finding("not_found", "not_found", text, facts_from(text))


def handle_attendance_projection(r: Runner, cls: Classification) -> list[Finding]:
    code = _single_course(r, cls)
    if isinstance(code, Finding):
        return [code]
    threshold = cls.slots.threshold_pct
    rules: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    basis = ""
    if threshold is None:
        tc_rule = r.call("get_rule", {"parameter": "min_attendance_pct"})
        res = RuleResolution.model_validate(tc_rule.output) if tc_rule.output else None
        if res is None or res.status != "resolved" or res.rule is None:
            return [_rule_problem("minimum attendance", res)]
        threshold = float(res.rule.value)
        rules.append(rule_payload(res.rule))
        citations.append(r.cite_rule(res.rule))
        conflicts = conflict_dicts(res.conflicts)
        basis = f" The threshold {res.rule.display} comes from {res.rule.rule_id} (clause {res.rule.source_section})."
    args = {"course_code": code, "threshold_pct": threshold}
    if cls.slots.future_classes:
        args["future_classes"] = cls.slots.future_classes
    tc = r.call("calculate_attendance_projection", args)
    if tc.status != "ok":
        return [_data_missing("attendance", tc, code)]
    o = tc.output or {}
    needed = o["classes_needed"]
    held = needed["inputs"]["classes_held"]
    attended = needed["inputs"]["classes_attended"]
    if needed["value"] is None:
        text = f"A threshold of {threshold:g}% cannot be reached in {code} once any class has been missed."
    elif needed["value"] == 0:
        text = (f"You are already at or above {threshold:g}% in {code}: {attended} of {held} classes attended "
                f"({attended / held * 100:.2f}%). No extra classes are needed.")
    else:
        n = needed["value"]
        text = (f"In {code} you have attended {attended} of {held} classes. To reach {threshold:g}% you need to attend the next "
                f"{n} classes in a row without missing any (then {attended + n} of {held + n}).")
    if o.get("classes_can_miss") and o["classes_can_miss"]["value"] is not None:
        m = o["classes_can_miss"]["value"]
        f = o["classes_can_miss"]["inputs"]["future_classes"]
        text += f" Of the next {f} classes you can miss at most {m} and stay at or above {threshold:g}%."
    elif o.get("classes_can_miss") and o["classes_can_miss"]["value"] is None:
        text += f" You cannot miss any of the next classes and still reach {threshold:g}%."
    text += basis
    return [Finding("projection", "calculated", text, facts_from(text), citations, rules, conflicts,
                    explanation="smallest n with (attended + n) x 100 >= threshold x (held + n); exact fraction arithmetic, no rounding before comparison")]


def _single_course(r: Runner, cls: Classification) -> str | Finding:
    codes = cls.slots.course_codes
    if len(codes) == 1:
        return codes[0]
    tc = r.call("get_attendance", {})
    options = [c["course_code"] for c in (tc.output or {}).get("courses", [])]
    if len(codes) > 1:
        return Finding("clarification", "clarification_needed",
                       f"Your question mentions more than one course ({', '.join(codes)}). Which one do you mean?", facts_from(" ".join(codes)))
    msg = "Which course do you mean?" + (f" Your courses are: {', '.join(options)}." if options else "")
    return Finding("clarification", "clarification_needed", msg, facts_from(" ".join(options)))


def _rule_problem(what: str, res: Optional[RuleResolution]) -> Finding:
    if res is None or res.status == "none":
        return Finding("not_found", "not_found", f"The {what} rule is not available in the rule registry, so I can't compute this.")
    if res.status == "clarification":
        return Finding("clarification", "clarification_needed", res.clarification or "Which programme and batch do you mean?")
    cd = conflict_dicts(res.conflicts)
    return Finding("conflict", "conflict_flagged", f"The sources disagree about the {what} and the precedence policy cannot decide. " + conflict_notes(cd),
                   facts_from(conflict_notes(cd)), [], [], cd)


# ------------------------------------------------------------------------------------------------
# Eligibility (exam, supplementary, placement, what-if)
# ------------------------------------------------------------------------------------------------
def _eligibility_finding(r: Runner, tc: ToolCall, subject: str, extra_citations: Optional[list[dict[str, Any]]] = None) -> Finding:
    from app.tools.eligibility import EligibilityOutput

    if tc.status != "ok":
        if tc.status == "not_found":
            return Finding("not_found", "not_found", f"I could not find the record needed to check {subject} ({tc.error}).")
        if tc.status == "denied":
            return Finding("not_found", "refused", "I can't access personal records without a valid student identity.")
        return Finding("not_found", "not_found", f"I could not complete the {subject} check ({tc.error}).")
    out = EligibilityOutput.model_validate(tc.output)
    rules = [rule_payload(x) for x in out.applied_rules]
    citations = [r.cite_rule(x) for x in out.applied_rules]
    conflicts = conflict_dicts(out.conflicts)
    for res in out.informational:
        conflicts += conflict_dicts(res.conflicts)
        for x in ([res.rule] if res.rule else []):
            if x.rule_id not in {y["rule_id"] for y in rules}:
                rules.append(rule_payload(x))
                citations.append(r.cite_rule(x, "informational"))
    unresolved = [c for c in conflicts if not c.get("resolved")]
    reasons = " ".join(out.reasons)
    head = {
        "ELIGIBLE": f"Yes, you are eligible {subject}.",
        "NOT_ELIGIBLE": f"No, you are not eligible {subject}.",
        "ELIGIBLE_WITH_CONDONATION": f"You are not eligible outright {subject}, but you may be eligible with condonation.",
        "INSUFFICIENT_DATA": f"I can't decide whether you are eligible {subject} because some data is missing.",
        "INSUFFICIENT_RULES": f"I can't decide whether you are eligible {subject} because no applicable rule is available in the rule registry.",
        "CONFLICT_UNRESOLVED": f"I can't decide whether you are eligible {subject}: the sources disagree and the precedence policy cannot choose between them.",
    }[out.result]
    parts = [head, reasons]
    note = conflict_notes([c for c in conflicts if c.get("resolved")])
    if note:
        parts.append(note)
    if unresolved:
        parts.append(conflict_notes(unresolved))
    if out.upcoming:
        parts.append(upcoming_notes(out.upcoming))
    for res in out.informational:
        if res.rule and res.status == "resolved":
            parts.append(f"{get_lexicon().label(res.parameter).capitalize()}: {human_value(res.rule)} ({res.rule.rule_id}).")
        elif res.status == "unresolved":
            parts.append(f"The {get_lexicon().label(res.parameter)} is stated differently in different documents; please confirm with the issuing office.")
    if out.needs_clarification:
        parts.append(out.needs_clarification)
    extra_assumptions = [a for a in out.assumptions if not a.startswith("Rules are applied as in force")]
    if extra_assumptions:
        parts.append("Assumptions: " + " ".join(extra_assumptions))
    text = " ".join(p for p in parts if p)
    if out.result == "CONFLICT_UNRESOLVED":
        atype = "conflict_flagged"
        kind = "conflict"
    elif out.result in ("INSUFFICIENT_DATA", "INSUFFICIENT_RULES"):
        atype, kind = ("clarification_needed" if out.needs_clarification else "not_found"), "not_found"
    else:
        atype, kind = "calculated", "eligibility"
    if unresolved and atype == "calculated":
        atype = "conflict_flagged"
    calc = out.calculation
    expl = (f"attendance = {calc.inputs['classes_attended']} / {calc.inputs['classes_held']} x 100 = {calc.value:g}%; " if calc else "") + \
           "; ".join(f"{c.name}: {c.actual} vs {c.operator or ''}{c.threshold or ''} -> {'pass' if c.passed else 'fail' if c.passed is False else 'n/a'}"
                     for c in out.checks)
    return Finding(kind, atype, text, facts_from(text + " " + expl), _dedupe(citations + (extra_citations or []), ("doc_id", "section", "role")),
                   rules, conflicts, [u.model_dump(mode="json") for u in out.upcoming], explanation=expl)


def handle_exam_eligibility(r: Runner, cls: Classification, exam_type: str = "REGULAR") -> list[Finding]:
    code = _single_course(r, cls)
    if isinstance(code, Finding):
        return [code]
    tc = r.call("check_exam_eligibility", {"course_code": code, "exam_type": exam_type})
    label = "regular end-semester exam" if exam_type == "REGULAR" else "supplementary exam"
    subject = f"to appear in the {label} for {code}"
    return [_eligibility_finding(r, tc, subject)]


def handle_placement(r: Runner, cls: Classification) -> list[Finding]:
    tc = r.call("check_placement_eligibility", {"assume_cleared_courses": cls.slots.assume_cleared})
    return [_eligibility_finding(r, tc, "for placements")]


def handle_what_if(r: Runner, cls: Classification) -> list[Finding]:
    pct = cls.slots.hypothetical_pct
    tc = r.call("check_attendance_band", {"attendance_pct": pct})
    return [_eligibility_finding(r, tc, f"to appear in the exam with {pct:g}% attendance (hypothetical)")]


# ------------------------------------------------------------------------------------------------
# CGPA, results, profile
# ------------------------------------------------------------------------------------------------
def handle_cgpa(r: Runner, cls: Classification) -> list[Finding]:
    tc = r.call("calculate_cgpa", {})
    if tc.status != "ok":
        return [_data_missing("CGPA", tc, None)]
    o = tc.output or {}
    stored = o.get("stored_cgpa")
    rules = [rule_payload(RuleInfo.model_validate(x)) for x in o.get("grade_scale_rules", [])]
    citations = _dedupe([r.cite_rule(RuleInfo.model_validate(x)) for x in o.get("grade_scale_rules", [])], ("doc_id", "section"))
    if o["status"] == "computed":
        calc = o["calculation"]
        text = (f"Your CGPA recomputed from your results is {o['recomputed_cgpa']:.2f} "
                f"(sum of credits x grade points / total credits of {calc['inputs']['total_credits']} credits, {o['courses_used']} courses).")
        if stored is not None:
            text += f" The official recorded CGPA is {stored:.2f}" + (" and it matches." if o.get("matches_record") else ".")
        if o.get("note"):
            text += " " + o["note"]
        return [Finding("cgpa", "calculated", text, facts_from(text), citations, rules,
                        explanation="CGPA = sum(credits x grade points) / sum(credits), grade points from the grade scale in the rule registry; rounded half up to 2 decimals")]
    if stored is not None:
        text = f"Your recorded CGPA is {stored:.2f}. " + (o.get("note") or "")
        return [Finding("cgpa", "calculated", text.strip(), facts_from(text), citations, rules, explanation="value read from the official student record")]
    return [Finding("not_found", "not_found", "No CGPA is recorded for you and it could not be computed.")]


def handle_results(r: Runner, cls: Classification) -> list[Finding]:
    code = cls.slots.course_codes[0] if len(cls.slots.course_codes) == 1 else None
    tc = r.call("get_results", {"course_code": code} if code else {})
    if tc.status != "ok":
        return [_data_missing("result", tc, code)]
    o = tc.output or {}
    status = [f"{s['course_code']}: {s['current_result']} ({s['latest_session']} {s['latest_exam_type'].lower()}, {s['attempts']} attempt{'s' if s['attempts'] != 1 else ''})"
              for s in o["course_status"]]
    text = "Your results: " + "; ".join(status) + "."
    text += (f" Courses not yet passed: {', '.join(o['backlog_courses'])}." if o["backlog_courses"] else " You have no pending backlog courses in these results.")
    return [Finding("results", "calculated", text, facts_from(text), explanation="status taken from the latest attempt for each course in the results table")]


def handle_profile(r: Runner, cls: Classification) -> list[Finding]:
    tc = r.call("get_student_profile", {})
    if tc.status != "ok":
        return [_data_missing("profile", tc, None)]
    o = tc.output or {}
    text = (f"You are in {o['programme']}, batch {o['batch_year']}, semester {o['current_semester']}. "
            f"Recorded CGPA: {o['cgpa']:.2f}; active backlogs: {o['active_backlogs']}." if o.get("cgpa") is not None else
            f"You are in {o['programme']}, batch {o['batch_year']}, semester {o['current_semester']}. Active backlogs: {o['active_backlogs']}.")
    return [Finding("profile", "calculated", text, facts_from(text), explanation="read from the student record")]


# ------------------------------------------------------------------------------------------------
# Policy parameters (rule registry)
# ------------------------------------------------------------------------------------------------
def handle_policy_parameter(r: Runner, cls: Classification) -> list[Finding]:
    findings: list[Finding] = []
    lex = get_lexicon()
    for param in cls.slots.parameters[:3]:
        args: dict[str, Any] = {"parameter": param}
        if cls.slots.programme:
            args["programme"] = cls.slots.programme
        if cls.slots.batch_year:
            args["batch_year"] = cls.slots.batch_year
        tc = r.call("get_rule", args)
        if tc.status != "ok" or not tc.output:
            continue
        res = RuleResolution.model_validate(tc.output)
        label = lex.label(param)
        label = label[:1].lower() + label[1:] if label[:2].upper() != label[:2] else label
        if res.status == "none":
            continue  # nothing in the registry: the retrieval path may still answer from document text
        if res.status == "clarification":
            findings.append(Finding("clarification", "clarification_needed", res.clarification or "Which programme and batch do you mean?",
                                    facts_from(res.clarification or ""), [], [], conflict_dicts(res.conflicts)))
            continue
        if res.status == "unresolved":
            cd = conflict_dicts(res.conflicts)
            tied = res.tied_rules
            vals = "; ".join(f"{t.source_doc_id} clause {t.source_section} says {human_value(t)}" for t in tied)
            text = f"The sources disagree about the {label} and the precedence policy cannot decide which applies: {vals}. " + conflict_notes([c for c in cd if not c.get("resolved")])
            findings.append(Finding("conflict", "conflict_flagged", text, facts_from(text), [r.cite_rule(t, "conflicting") for t in tied],
                                    [rule_payload(t) for t in tied], cd))
            continue
        rule = res.rule
        assert rule is not None
        text = f"The {label} is {human_value(rule)} ({rule.rule_id}, clause {rule.source_section} of {_title(r, rule.source_doc_id)}, in force on {res.as_of})."
        cd = conflict_dicts(res.conflicts)
        note = conflict_notes([c for c in cd if c.get("resolved")])
        if note:
            text += " " + note
        if res.upcoming:
            text += " " + upcoming_notes(res.upcoming)
        cites = [r.cite_rule(rule)]
        findings.append(Finding("policy_rule", "retrieved_fact", text, facts_from(text), _dedupe(cites, ("doc_id", "section", "role")),
                                [rule_payload(rule)], cd, [u.model_dump(mode="json") for u in res.upcoming],
                                explanation="; ".join(res.trace[-3:])))
    return findings


def _title(r: Runner, doc_id: str) -> str:
    doc = r.ctx.docs().get(doc_id)
    return doc.title if doc else doc_id


HANDLERS = {
    "attendance_status": handle_attendance_status,
    "attendance_projection": handle_attendance_projection,
    "attendance_what_if": handle_what_if,
    "exam_eligibility": lambda r, c: handle_exam_eligibility(r, c, "REGULAR"),
    "supplementary_eligibility": lambda r, c: handle_exam_eligibility(r, c, "SUPPLEMENTARY"),
    "placement_eligibility": handle_placement,
    "cgpa": handle_cgpa,
    "results_status": handle_results,
    "student_profile": handle_profile,
    "policy_parameter": handle_policy_parameter,
}
