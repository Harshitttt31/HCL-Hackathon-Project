"""Deterministic intent and slot extraction. Rules first; the LLM may only break a tie between allowlisted intents."""
from __future__ import annotations

import re
from typing import Optional

from app.agent.state import INTENTS, PERSONAL_INTENTS, Classification, Slots
from app.rag.claims import get_lexicon
from app.rag.chunker import detect_injection

COURSE_RE = re.compile(r"\b([A-Za-z]{2,4})[\s-]?(\d{3}[A-Za-z]?)\b")
FIRST_PERSON_RE = re.compile(r"\b(i|i'm|im|i've|ive|i'll|my|me|mine|myself)\b", re.IGNORECASE)
PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent|per cent|pct)", re.IGNORECASE)
NUM_CLASSES_RE = re.compile(r"\b(?:next|upcoming|coming|remaining|future)\s+(\d{1,3})\s+(?:classes|lectures|sessions|periods)", re.IGNORECASE)
WHAT_IF_RE = re.compile(r"\b(what if|suppose|assume|assuming|if (?:i|my|the)|had i|would (?:i|it)|were to|in case)\b", re.IGNORECASE)

ELIG_RE = re.compile(r"\b(eligible|eligibility|allowed to|permitted to|can i (?:appear|sit|write|take|attend)|may i (?:appear|sit|write)|"
                     r"appear (?:for|in)|sit (?:for|in)|write the exam|hall ticket|detain|debar|condon|barred|qualif)", re.IGNORECASE)
EXAM_RE = re.compile(r"\b(exam|examination|end[- ]?sem|semester exam|finals?|hall ticket)\b", re.IGNORECASE)
SUPP_RE = re.compile(r"\b(supplementary|supply exam|supply|re-?exam|re-?appear|reappear|arrear|improvement exam)\b", re.IGNORECASE)
PLACEMENT_RE = re.compile(r"\b(placement|placements|campus (?:drive|recruitment|hiring)|recruit(?:ment|er|ers)?|company drive|sit for placements?)\b", re.IGNORECASE)
CGPA_RE = re.compile(r"\b(cgpa|gpa|cumulative grade|grade point average|sgpa)\b", re.IGNORECASE)
RESULT_RE = re.compile(r"\b(result|results|marks|grade|grades|backlog|backlogs|arrears?|failed|fail|passed|pass|cleared|clear)\b", re.IGNORECASE)
ATTEND_RE = re.compile(r"\b(attend\w*|classes (?:held|missed)|miss|missing|skip|bunk|absent|shortage|present)\b", re.IGNORECASE)
PAST_ATT_RE = re.compile(r"\b(?:have|did|had) i (?:attended|attend|been present)\b|\bi (?:have )?attended\b", re.IGNORECASE)
NEED_RE = re.compile(r"\b(?:need|must|should|to reach|to get|to be|more)\b", re.IGNORECASE)
POLICY_ASK_RE = re.compile(r"\b(minimum|required|requirement|mandatory|threshold|cut-?off|rule|rules|policy|limit)\b", re.IGNORECASE)
MY_RECORD_RE = re.compile(r"\b(?:my (?:attendance|record|percentage|classes)|have i|did i|am i|do i have)\b", re.IGNORECASE)
PROJECTION_RE = re.compile(r"\b(how many (?:more |further |additional )?(?:classes|lectures|sessions|days)|classes (?:do i |must i |should i |i )?(?:need|have|must|should)|"
                           r"need to attend|have to attend|must attend|to reach|to get (?:to|back|above)|to be (?:eligible|above|at)|can i (?:miss|skip|bunk)|"
                           r"how many .{0,25}(?:miss|skip|bunk)|afford to miss|reach \d)", re.IGNORECASE)
PROFILE_RE = re.compile(r"\b(my (?:programme|program|batch|semester|profile|details|record)|which (?:programme|program|batch|semester)(?: and (?:programme|program|batch|semester))* am i)\b", re.IGNORECASE)
CLEAR_WHATIF_RE = re.compile(r"\b(?:if|once|after|when|suppose|assuming)\b[^.?!]{0,60}\b(?:clear|pass|cleared|passed|clearing|passing)\b", re.IGNORECASE)
THRESHOLD_CONTEXT_RE = re.compile(r"(?:reach|get (?:to|back to|above)|attain|achieve|be at|at least|minimum of|required|requirement of|up to|above|to)\s+(?:an?\s+|the\s+)?(?:attendance\s+(?:of\s+)?)?(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)

SUPP_DEADLINE_HINT = re.compile(r"\b(deadline|last date|fee|fees|cost|apply|application|register|registration)\b", re.IGNORECASE)

PROGRAMME_ALIASES = {
    r"\bb\.?\s?tech\s*(?:in\s*)?(?:cse|computer science(?: and engineering)?)\b": "B.Tech CSE",
    r"\bb\.?\s?tech\s*(?:in\s*)?(?:ece|electronics(?: and communication)?(?: engineering)?)\b": "B.Tech ECE",
    r"\bb\.?\s?tech\s*(?:in\s*)?(?:me|mechanical(?: engineering)?)\b": "B.Tech ME",
    r"\bm\.?\s?tech\b": "M.Tech",
    r"\bmba\b": "MBA",
    r"\bbca\b": "BCA",
    r"\bb\.?\s?sc\b": "B.Sc",
}
BATCH_RE = re.compile(r"\b(?:batch|admitted in|admission(?: year)?|joined in|class of|cohort)\s*(?:of\s*)?(\d{4})\b|\b(\d{4})\s+(?:batch|admission|intake|cohort)\b", re.IGNORECASE)

INJECTION_EXTRA = re.compile(
    r"(?:you are now|act as (?:an? )?(?:admin|administrator|dean|registrar|root)|developer mode|jailbreak|do anything now|"
    r"pretend (?:to be|you are)|bypass (?:the )?(?:rules|checks|privacy|restrictions)|sudo\b|"
    r"ignore (?:the )?(?:rules|policy|policies|restrictions)|from now on,? you|simulate (?:an? )?(?:admin|unrestricted))", re.IGNORECASE)


def question_injection(text: str) -> bool:
    return detect_injection(text) or bool(INJECTION_EXTRA.search(text))


def _course_codes(text: str, names: dict[str, str]) -> list[str]:
    codes: list[str] = []
    for m in COURSE_RE.finditer(text):
        prefix = m.group(1).upper()
        if prefix in {"AND", "THE", "FOR", "ALL"}:
            continue
        code = f"{prefix}{m.group(2).upper()}"
        if code not in codes:
            codes.append(code)
    low = text.lower()
    for name, code in names.items():
        if len(name) >= 5 and re.search(rf"\b{re.escape(name)}\b", low) and code not in codes:
            codes.append(code)
    return codes


def extract_slots(question: str, course_names: Optional[dict[str, str]] = None) -> Slots:
    q = re.sub(r"\s+", " ", question).strip()
    s = Slots()
    s.first_person = bool(FIRST_PERSON_RE.search(q))
    s.course_codes = _course_codes(q, course_names or {})
    if SUPP_RE.search(q):
        s.exam_type = "SUPPLEMENTARY"
    elif EXAM_RE.search(q) or ELIG_RE.search(q):
        s.exam_type = "REGULAR"
    s.parameters = get_lexicon().detect_query_parameters(q)
    pcts = [float(x) for x in PCT_RE.findall(q)]
    m = THRESHOLD_CONTEXT_RE.search(q)
    if m:
        s.threshold_pct = float(m.group(1))
    if pcts and ATTEND_RE.search(q) and WHAT_IF_RE.search(q) and not m:
        s.hypothetical_pct = pcts[0]
    elif pcts and ATTEND_RE.search(q) and s.threshold_pct is None and s.first_person and not PROJECTION_RE.search(q):
        s.hypothetical_pct = pcts[0] if WHAT_IF_RE.search(q) else None
    fm = NUM_CLASSES_RE.search(q)
    if fm:
        s.future_classes = int(fm.group(1))
    if CLEAR_WHATIF_RE.search(q):
        # courses named inside the "if I clear ..." part are the ones assumed cleared
        start = CLEAR_WHATIF_RE.search(q).start()
        seg = re.split(r"[,?.;]", q[start:], maxsplit=1)[0]
        s.assume_cleared = [c for c in _course_codes(seg, course_names or {})]
    for pattern, prog in PROGRAMME_ALIASES.items():
        if re.search(pattern, q, re.IGNORECASE):
            s.programme = prog
            break
    b = BATCH_RE.search(q)
    if b:
        year = int(b.group(1) or b.group(2))
        if 1990 <= year <= 2100:
            s.batch_year = year
    return s


def classify(question: str, course_names: Optional[dict[str, str]] = None) -> Classification:
    q = re.sub(r"\s+", " ", question).strip()
    slots = extract_slots(q, course_names)
    c = Classification(slots=slots, injection_flag=question_injection(q))
    intents: list[str] = []
    notes: list[str] = []

    has_att = bool(ATTEND_RE.search(q))
    has_elig = bool(ELIG_RE.search(q))
    has_supp = bool(SUPP_RE.search(q))
    has_plc = bool(PLACEMENT_RE.search(q))
    has_cgpa = bool(CGPA_RE.search(q))
    has_result = bool(RESULT_RE.search(q))
    has_proj = bool(PROJECTION_RE.search(q)) and has_att
    if has_proj and PAST_ATT_RE.search(q) and not NEED_RE.search(q):
        has_proj = False  # "how many classes have I attended" is a status question, not a projection
    first = slots.first_person
    personal_hint = first or bool(PROFILE_RE.search(q))
    # "attendance CS 301": a course code next to an attendance word is a request for the student's own record
    own_record = first or bool(slots.course_codes)
    # "what is the minimum attendance I need": a policy question even though it contains "I"
    policy_ask = bool(POLICY_ASK_RE.search(q)) and bool(slots.parameters) and not slots.course_codes and not MY_RECORD_RE.search(q.replace("I need", "").replace("i need", ""))

    # --- what-if attendance percentage (no record needed) ---------------------------------------------------------
    if slots.hypothetical_pct is not None and (has_elig or has_att):
        intents.append("attendance_what_if")
        notes.append("hypothetical attendance percentage in the question")

    # --- personal intents --------------------------------------------------------------------------------------------
    if has_plc and (first or slots.assume_cleared) and not policy_ask:
        intents.append("placement_eligibility")
    if has_supp and personal_hint and (has_elig or re.search(r"\b(can|may|could|should|am) i\b", q, re.I)):
        intents.append("supplementary_eligibility")
    elif has_elig and not has_plc and not has_supp and (first or slots.course_codes) and "attendance_what_if" not in intents:
        if EXAM_RE.search(q) or slots.course_codes or re.search(r"\b(condon|detain|debar|barred)\b", q, re.I):
            intents.append("exam_eligibility")
    if has_proj and (first or slots.course_codes) and "attendance_projection" not in intents:
        intents.append("attendance_projection")
    asks_status = has_att and own_record and not has_proj and not policy_ask
    if asks_status and not intents:
        intents.append("attendance_status")
    elif asks_status and "attendance_status" not in intents and not has_elig and slots.hypothetical_pct is None:
        intents.append("attendance_status")
    if has_cgpa and first:
        intents.append("cgpa")
    if has_result and first and not has_cgpa and not has_plc and not has_supp and not has_att and "results_status" not in intents:
        intents.append("results_status")
    if PROFILE_RE.search(q) and not intents:
        intents.append("student_profile")

    # --- policy intents ---------------------------------------------------------------------------------------------------
    personal_present = any(i in PERSONAL_INTENTS for i in intents)
    if slots.parameters and "attendance_what_if" not in intents and (not personal_present or (POLICY_ASK_RE.search(q) and personal_present and len(intents) == 1 and "attendance_status" in intents)):
        intents.append("policy_parameter")
    if not intents:
        intents.append("policy_text")
    elif not personal_present and "policy_parameter" not in intents and "attendance_what_if" not in intents:
        pass

    # stable order, no duplicates
    seen, ordered = set(), []
    for i in intents:
        if i not in seen and i in INTENTS:
            seen.add(i)
            ordered.append(i)
    c.intents = ordered
    c.notes = notes
    c.confidence = 0.9 if ordered != ["policy_text"] else 0.5
    c.source = "rules" if ordered != ["policy_text"] else "none"
    return c
