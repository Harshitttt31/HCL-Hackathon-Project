"""Privacy checker. Runs before any tool is planned or any document is searched.

Rules (guide R7 and the privacy requirements):
  * The student identity comes ONLY from the X-Student-Id header. Text in the question never changes it.
  * A personal question without an identity is refused.
  * A question about another named or identified student (id, name, relation word, or a class-wide list) is refused.
  * Refusals never reveal whether the other student exists, and never echo their identifier.
"""
from __future__ import annotations

import re
from typing import Optional

from app.agent.state import Classification, PrivacyVerdict
from app.core.security import ID_IN_TEXT_RE, normalize_student_id
from app.database.repositories import StudentRepo

RELATION_RE = re.compile(
    r"\b(?:my|his|her|their)\s+(?:friend|friends|roommate|room mate|classmate|classmates|batchmate|batch mate|brother|sister|cousin|senior|junior|"
    r"girlfriend|boyfriend|partner|neighbou?r|colleague|mentee|son|daughter|ward)\b|"
    r"\b(?:another|other|some(?:one|body)(?: else)?|a different)\s+(?:student|person|classmate|candidate)\b|"
    r"\b(?:he|she|they|him|them)\s+(?:has|have|is|are|was|were|got|scored|attended|failed|passed|will|can|could)\b|"
    r"\b(?:his|her|their)\s+(?:attendance|marks|result|results|cgpa|grade|grades|backlogs?|record|records|details|score|scores|profile)\b",
    re.IGNORECASE)
_DATA_NOUN = r"(?:attendance|marks|cgpa|gpa|results?|grades?|records?|details|scores?|backlogs?|names?|ids?|addresses|phone|emails?)"
BULK_RE = re.compile(
    rf"\b(?:all|every|each|other|everyone'?s|everybody'?s)\s+(?:the\s+)?(?:students?|batch|class|classmates|candidates)'?s?\s+{_DATA_NOUN}\b|"
    r"\b(?:list|show|give|tell|display|export|dump|name)\b[^.?!]{0,30}\b(?:all|every|each|other|the)?\s*(?:students?|everyone|everybody|classmates|all records)\b|"
    rf"\b{_DATA_NOUN}\s+(?:of|for)\s+(?:every|each|all|any)\s+(?:single\s+)?(?:the\s+)?(?:students?|classmates)\b|"
    r"\b(?:everyone'?s|everybody'?s|class average|batch average|class topper|toppers?|rank list|rank holders?|"
    r"who (?:has|have|got|is|are|scored) (?:the )?(?:highest|lowest|best|worst|top|failed|passed|below|above))\b|"
    r"\bhow many students\b|\bstudents (?:with|who|having)\b[^.?!]{0,40}\b(?:attendance|cgpa|backlogs?|below|above|failed|detained)\b",
    re.IGNORECASE)
POSSESSIVE_NAME_TMPL = r"\b{name}(?:'s|s')?\b"


def check_privacy(question: str, student_id: Optional[str], cls: Classification, student_header_valid: bool) -> PrivacyVerdict:
    """student_header_valid: the header, if present, was well-formed (S plus four digits)."""
    own = normalize_student_id(student_id) if student_id else None
    q = question or ""

    # identifiers written in the question
    mentioned = {m.group(0).upper().replace(" ", "").replace("-", "").replace("_", "") for m in ID_IN_TEXT_RE.finditer(q)}
    others = {m for m in mentioned if m != own}
    if others:
        return PrivacyVerdict(False, "I can only share your own academic records. I can't look up or discuss another student's data.",
                              "other_student", "student identifier of another person written in the question")

    if BULK_RE.search(q):
        return PrivacyVerdict(False, "I can't share information about other students or groups of students. I can answer questions about your own records and about university policy.",
                              "bulk_request", "request for other students' or class-wide data")

    lex = StudentRepo().identity_lexicon()
    own_names: set[str] = set()
    if own:
        own_student = StudentRepo().get(own)
        if own_student:
            full = own_student.full_name.lower()
            own_names = {full} | {p for p in full.split() if len(p) >= 3}
    low = q.lower()
    for name in sorted(lex["names"], key=len, reverse=True):
        if name in own_names:
            continue
        if re.search(rf"\b{re.escape(name)}\b", low):
            # a name that is also an ordinary word in the question is only treated as a person if it is a full name or capitalised
            if " " in name or re.search(rf"\b{re.escape(name.capitalize())}\b", q):
                return PrivacyVerdict(False, "I can only share your own academic records. I can't look up or discuss another student's data.",
                                      "other_student", "a known student's name appears in the question")

    if RELATION_RE.search(q) and (re.search(rf"\b{_DATA_NOUN}\b|eligib|status|detain|debar|fail|pass|placement|exam", q, re.IGNORECASE) or cls.personal):
        return PrivacyVerdict(False, "I can only share your own academic records. I can't look up or discuss another person's data.",
                              "other_student", "reference to another person (relation word or pronoun)")

    if cls.personal:
        if not own:
            msg = ("I need to know who you are to answer questions about your own records. Please log in with your student identifier "
                   "(the X-Student-Id header) and ask again." if not student_id else
                   "The student identifier supplied is not valid, so I can't look up personal records.")
            return PrivacyVerdict(False, msg, "no_identity", "personal question without a valid identity")
    return PrivacyVerdict(True)
