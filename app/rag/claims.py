"""Parameter lexicon: extract rule claims from text and recognise which parameter a question is about.

How a rule gets into the rule registry (guide: "be ready to explain"):
  1. A document is ingested through POST /ingest (or scripts/ingest.py) with its Source Register metadata.
  2. Each chunk is scanned with the lexicon patterns. A match becomes a rule_registry row with origin='extracted',
     the verbatim quote, the chunk, the section and page, and the document's scope and effective dates.
  3. The row points at source_doc_id in the Source Register, which supplies authority level and supersession.
  4. At question time the precedence engine resolves all rows of a parameter. When a new circular changes a rule,
     its rows simply join the candidate set: if the circular supersedes the old clause (or is the higher/later
     authority) it wins; otherwise the conflict is reported. No code change, no manual edit.
Curated rows (data/rule_registry.csv) are also supported and are never overwritten by extraction.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.models import ChunkRow, RuleRow
from app.sources.models import SourceMetadata

log = get_logger(__name__)
LEXICON_PATH = Path(__file__).with_name("parameter_lexicon.yaml")

_WORD_NUM = {"zero": "0", "no": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
             "eight": "8", "nine": "9", "ten": "10", "active": "0"}
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}


@dataclass
class Claim:
    parameter: str
    operator: str
    value: str
    unit: str
    quote: str
    chunk_id: str
    section: str
    page: Optional[int]
    label: str
    prefix: str


def parse_date_text(text: str) -> Optional[str]:
    t = text.strip().rstrip(".,")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9}),?\s+(\d{4})", t)
    if m:
        day, mon, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    else:
        m = re.fullmatch(r"([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t)
        if not m:
            return None
        mon, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
    month = next((n for name, n in _MONTHS.items() if name.startswith(mon[:3]) and name.startswith(mon) or name == mon), None)
    if month is None:
        month = next((n for name, n in _MONTHS.items() if name.startswith(mon[:3])), None)
    if month is None:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def normalise_value(raw: str, unit: str) -> Optional[str]:
    raw = raw.strip()
    if unit == "date":
        return parse_date_text(raw)
    low = raw.lower()
    if low in _WORD_NUM:
        return _WORD_NUM[low]
    cleaned = raw.replace(",", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        # keep the number as written but drop a redundant trailing ".0"
        return cleaned[:-2] if cleaned.endswith(".0") else cleaned
    return None


def _sentence_around(text: str, start: int, end: int, limit: int = 360) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    left = left + 1 if left != -1 else 0
    right_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    quote = re.sub(r"\s+", " ", text[left:right]).strip()
    if len(quote) > limit:
        mid = start - left
        lo = max(0, mid - limit // 2)
        quote = quote[lo: lo + limit].strip()
    return quote


class ParameterLexicon:
    def __init__(self, spec: dict[str, Any]):
        self.params: dict[str, dict[str, Any]] = {}
        for key, cfg in (spec.get("parameters") or {}).items():
            compiled = [re.compile(p["regex"]) for p in cfg.get("patterns", [])]
            self.params[key] = {
                **cfg,
                "_patterns": compiled,
                "_query": [re.compile(p, re.IGNORECASE) for p in cfg.get("query_patterns", [])],
                "_exclude": [re.compile(p, re.IGNORECASE) for p in cfg.get("exclude_query_patterns", [])],
            }

    def keys(self) -> list[str]:
        return list(self.params)

    def label(self, key: str) -> str:
        return self.params.get(key, {}).get("label", key.replace("_", " "))

    def unit(self, key: str) -> str:
        return self.params.get(key, {}).get("unit", "")

    def detect_query_parameters(self, query: str) -> list[str]:
        """Parameters the question is about, most specific first (YAML order)."""
        hits = []
        for key, cfg in self.params.items():
            if any(r.search(query) for r in cfg["_exclude"]):
                continue
            if any(r.search(query) for r in cfg["_query"]):
                hits.append(key)
        return hits

    def extract(self, chunk: ChunkRow) -> list[Claim]:
        """Rule claims stated in one chunk (tables are scanned in their serialised sentence form)."""
        out: list[Claim] = []
        text = chunk.text
        seen: set[tuple[str, str]] = set()
        for key, cfg in self.params.items():
            for rx in cfg["_patterns"]:
                for m in rx.finditer(text):
                    value = normalise_value(m.group("value"), cfg.get("unit", ""))
                    if value is None or (key, value) in seen:
                        continue
                    seen.add((key, value))
                    out.append(Claim(parameter=key, operator=cfg.get("operator", "=="), value=value, unit=cfg.get("unit", ""),
                                     quote=_sentence_around(text, m.start(), m.end()), chunk_id=chunk.chunk_id,
                                     section=chunk.section_number or chunk.section_title, page=chunk.page,
                                     label=cfg.get("label", key), prefix=cfg.get("prefix", key[:8].upper())))
        return out

    def extract_grade_bands(self, chunk: ChunkRow) -> list[dict[str, Any]]:
        """Grade scale rows from a table with marks-range, grade and grade-point columns."""
        rows = chunk.table_json or []
        if len(rows) < 2:
            return []
        header = [h.lower() for h in rows[0]]

        def col(*needles: str) -> Optional[int]:
            for i, h in enumerate(header):
                if any(n in h for n in needles):
                    return i
            return None

        c_marks, c_grade, c_points = col("marks", "range", "percentage", "%"), col("letter", "grade"), col("point")
        if c_marks is None or c_grade is None or c_points is None or len({c_marks, c_grade, c_points}) < 3:
            # "Grade" alone may hold the letter while "Grade points" holds the number
            g = [i for i, h in enumerate(header) if "grade" in h and "point" not in h]
            p = [i for i, h in enumerate(header) if "point" in h]
            if not (g and p and c_marks is not None):
                return []
            c_grade, c_points = g[0], p[0]
        bands: list[dict[str, Any]] = []
        for r in rows[1:]:
            if max(c_marks, c_grade, c_points) >= len(r):
                continue
            rng = r[c_marks].replace("–", "-").replace("\u2014", "-")
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", rng)]
            if not nums:
                continue
            below = bool(re.search(r"(?i)below|less than|under|<", rng))
            if below:
                lo, hi = 0.0, nums[0]
            elif len(nums) >= 2:
                lo, hi = nums[0], nums[1]
            else:
                lo, hi = nums[0], 100.0
            try:
                points = float(re.findall(r"\d+(?:\.\d+)?", r[c_points])[0])
            except (IndexError, ValueError):
                continue
            grade = r[c_grade].strip()
            if grade:
                bands.append({"grade": grade, "min": lo, "max": hi, "points": points})
        return bands if len(bands) >= 3 else []


_lock = threading.Lock()
_lexicon: Optional[ParameterLexicon] = None


def get_lexicon(force_reload: bool = False) -> ParameterLexicon:
    global _lexicon
    with _lock:
        if _lexicon is None or force_reload:
            with open(LEXICON_PATH, encoding="utf-8") as fh:
                _lexicon = ParameterLexicon(yaml.safe_load(fh))
        return _lexicon


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").upper()


def claims_to_rules(claims: list[Claim], meta: SourceMetadata, existing_curated: set[tuple[str, str, str]]) -> list[RuleRow]:
    """Turn extracted claims into rule_registry rows tied to the document and clause they came from."""
    rules: list[RuleRow] = []
    used: set[str] = set()
    for c in claims:
        if (meta.doc_id, c.parameter, c.section) in existing_curated:
            continue  # a curated row for this clause wins; extraction never overwrites it
        base = f"{c.prefix}-{_slug(meta.doc_id)}-{_slug(c.section) or 'X'}"
        rule_id, n = base, 1
        while rule_id in used:
            n += 1
            rule_id = f"{base}-{n}"
        used.add(rule_id)
        rules.append(RuleRow(
            rule_id=rule_id, description=f"{c.label}: {c.quote}"[:500], parameter=c.parameter, operator=c.operator, value=c.value,
            unit=c.unit, scope_programmes=meta.scope_programmes, scope_batches=meta.scope_batches,
            effective_from=meta.effective_from, effective_to=meta.effective_to, source_doc_id=meta.doc_id,
            source_section=c.section or "n/a", origin="extracted", quote=c.quote, chunk_id=c.chunk_id, source_page=c.page))
    return rules


def grade_band_rules(bands: list[dict[str, Any]], chunk: ChunkRow, meta: SourceMetadata) -> list[RuleRow]:
    rules = []
    for b in bands:
        rules.append(RuleRow(
            rule_id=f"GRD-{_slug(meta.doc_id)}-{_slug(str(b['grade']))}", description=f"Grade {b['grade']}: {b['min']:g} to {b['max']:g} percent = {b['points']:g} grade points",
            parameter="grade_points", operator="between", value=f"{b['min']:g}-{b['max']:g}", unit="grade_points",
            scope_programmes=meta.scope_programmes, scope_batches=meta.scope_batches, effective_from=meta.effective_from,
            effective_to=meta.effective_to, source_doc_id=meta.doc_id, source_section=chunk.section_number or chunk.section_title or "n/a",
            attributes={"grade": b["grade"], "min": b["min"], "max": b["max"], "points": b["points"]}, origin="extracted",
            quote=chunk.text[:300], chunk_id=chunk.chunk_id, source_page=chunk.page))
    return rules
