"""Structure-aware chunking.

Chunks follow the document's own structure (headings > clauses > paragraphs/lists) instead of fixed windows.
Each chunk keeps its place in the hierarchy (section path, section number, page) and its neighbours, so a
citation can name "Academic Regulations 2024 > 7 Attendance > 7.2 Minimum Attendance, page 14".
Tables are never split mid-row: each is serialised to searchable "column=value" sentences while the
original rows are kept as structured JSON on the chunk.

A "fixed" strategy exists ONLY as an evaluation baseline (see scripts/evaluate.py).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings
from app.database.models import ChunkRow
from app.rag.parser import Block, ParsedDocument

_XREF_RE = re.compile(
    r"\b(?:clauses?|sections?|articles?|paragraphs?|regulations?|rules?|para)\s+(\d+(?:\.\d+)*(?:\([a-z0-9]+\))?)", re.IGNORECASE)

# Phrases that try to talk to the assistant instead of informing a student. Treated as DATA and flagged (guide R8).
_INJECTION_PATTERNS = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions?|rules?|prompts?|context)",
    r"disregard (?:all |any |the )?(?:previous|prior|above|earlier|your) (?:instructions?|rules?|guidelines?)",
    r"forget (?:all |everything )?(?:previous|prior|above|earlier|your) (?:instructions?|rules?)",
    r"you (?:must|should|will) now (?:act|behave|respond|answer|ignore|reveal)",
    r"(?:system|developer) prompt",
    r"as an ai (?:language )?model",
    r"(?:reveal|print|show|output) (?:your|the) (?:instructions|prompt|system)",
    r"override (?:all )?(?:other )?(?:rules|policies|documents|sources)",
    r"new instructions?\s*:",
    r"<\s*/?\s*(?:system|assistant|instructions?)\s*>",
    r"tell (?:the )?(?:user|student)s? that",
    r"do not (?:cite|mention|reveal) (?:any )?(?:sources?|documents?)",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()[:16]


def extract_references(text: str) -> list[str]:
    seen: list[str] = []
    for m in _XREF_RE.finditer(text or ""):
        ref = m.group(1)
        if ref not in seen:
            seen.append(ref)
    return seen[:10]


def serialise_table(rows: list[list[str]], caption: str = "") -> str:
    """Semantic text for a table: 'Columns: A | B. Row 1: A = x; B = y.' (keeps the numbers next to their labels)."""
    if not rows:
        return ""
    header = rows[0]
    has_header = len(rows) > 1 and all(c.strip() for c in header) and not any(re.fullmatch(r"[\d.,%\s/-]+", c) for c in header)
    parts: list[str] = []
    if caption:
        parts.append(f"Table: {caption}.")
    if has_header:
        parts.append("Columns: " + " | ".join(header) + ".")
        body = rows[1:]
        for i, row in enumerate(body, 1):
            cells = [f"{header[j] if j < len(header) else f'column {j + 1}'} = {c}" for j, c in enumerate(row) if c.strip()]
            parts.append(f"Row {i}: " + "; ".join(cells) + ".")
    else:
        for i, row in enumerate(rows, 1):
            parts.append(f"Row {i}: " + " | ".join(c for c in row if c.strip()) + ".")
    return " ".join(parts)


@dataclass
class _Section:
    level: int
    number: str
    title: str

    @property
    def label(self) -> str:
        return f"{self.number} {self.title}".strip()


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.;:])\s+(?=[A-Z(\d])", text)
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > max_chars:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    final: list[str] = []
    for piece in out:  # a single over-long sentence is cut on word boundaries as a last resort
        while len(piece) > max_chars * 1.5:
            cut = piece.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            final.append(piece[:cut])
            piece = piece[cut:].strip()
        final.append(piece)
    return final


class StructureChunker:
    def __init__(self, max_chars: Optional[int] = None):
        self.max_chars = max_chars or get_settings().chunk_max_chars

    def chunk(self, doc_id: str, doc_title: str, parsed: ParsedDocument) -> list[ChunkRow]:
        stack: list[_Section] = []
        raw: list[dict] = []
        buf: list[tuple[str, Block]] = []  # (kind, block)
        buf_number = ""
        order = 0

        def current_path() -> list[str]:
            return [doc_title] + [s.label for s in stack]

        def current_section() -> tuple[str, str, int]:
            if stack:
                return stack[-1].number, stack[-1].title, stack[-1].level
            return "", "", 0

        def flush() -> None:
            nonlocal buf, buf_number
            if not buf:
                return
            number, title, level = current_section()
            if buf_number:
                number = buf_number
            body = "\n".join(b.text for _, b in buf)
            pages = [b.page for _, b in buf if b.page]
            ocr_flag = any(b.ocr for _, b in buf)
            conf = min((b.confidence for _, b in buf), default=1.0)
            kind = "list" if all(k == "list_item" for k, _ in buf) else "paragraph"
            for piece in _split_long(body, self.max_chars):
                raw.append({"text": piece, "kind": kind, "number": number, "title": title, "level": level,
                            "path": current_path() + ([buf_number] if buf_number and buf_number != number_of_stack() else []),
                            "page": pages[0] if pages else None, "ocr": ocr_flag, "conf": conf, "rows": None})
            buf = []
            buf_number = ""

        def number_of_stack() -> str:
            return stack[-1].number if stack else ""

        for block in parsed.blocks:
            if block.kind == "heading":
                flush()
                level = block.level or 1
                while stack and stack[-1].level >= level:
                    stack.pop()
                stack.append(_Section(level, block.number, block.text))
                continue
            if block.kind == "table":
                flush()
                number, title, level = current_section()
                caption = title or doc_title
                rows = block.rows or []
                step = 40
                parts = [rows] if len(rows) <= step else [
                    (rows[:1] + rows[i:i + step]) if i else rows[:step] for i in range(0, len(rows), step)]
                for part in parts:
                    raw.append({"text": serialise_table(part, caption), "kind": "table", "number": number, "title": title,
                                "level": level, "path": current_path(), "page": block.page, "ocr": block.ocr,
                                "conf": block.confidence, "rows": part})
                continue
            if block.kind == "clause":
                # a numbered clause opens its own chunk (granular retrieval and precise citation)
                if buf and (buf_number or sum(len(b.text) for _, b in buf) > 80):
                    flush()
                elif buf:
                    pass  # tiny lead-in text stays attached to the first clause
                buf_number = block.number
                buf.append(("paragraph", block))
                continue
            # paragraph or list item
            current_len = sum(len(b.text) for _, b in buf)
            if buf and current_len + len(block.text) > self.max_chars and block.kind != "list_item":
                flush()
            buf.append((block.kind, block))
        flush()

        rows_out: list[ChunkRow] = []
        total = len(raw)
        for i, c in enumerate(raw):
            cid = f"{doc_id}::{i:04d}"
            path = [p for p in c["path"] if p]
            embed_text = f"{doc_title} | {' > '.join(path[1:]) if len(path) > 1 else ''}\n{c['text']}".replace("|  \n", "|\n")
            rows_out.append(ChunkRow(
                chunk_id=cid, doc_id=doc_id, ordinal=i, section_number=c["number"], section_title=c["title"],
                section_path=path, heading_level=c["level"], page=c["page"], kind=c["kind"], text=c["text"],
                embed_text=embed_text, table_json=c["rows"], references=extract_references(c["text"]),
                prev_chunk_id=f"{doc_id}::{i - 1:04d}" if i > 0 else None,
                next_chunk_id=f"{doc_id}::{i + 1:04d}" if i < total - 1 else None,
                content_hash=content_hash(c["text"]), injection_suspected=detect_injection(c["text"]),
                confidence=round(float(c["conf"]), 3), ocr=c["ocr"],
            ))
        return rows_out


class FixedChunker:
    """Baseline: fixed-size windows over the flattened text, no hierarchy. Used only for the chunking comparison."""

    def __init__(self, size: Optional[int] = None, overlap: int = 50):
        self.size = size or get_settings().fixed_chunk_chars
        self.overlap = overlap

    def chunk(self, doc_id: str, doc_title: str, parsed: ParsedDocument) -> list[ChunkRow]:
        pieces: list[tuple[str, Optional[int]]] = []
        for b in parsed.blocks:
            text = b.text if b.kind != "table" else serialise_table(b.rows or [])
            pieces.append((f"{b.number} {text}".strip() if b.number else text, b.page))
        flat = " ".join(p for p, _ in pieces)
        out: list[ChunkRow] = []
        start, i = 0, 0
        while start < len(flat):
            end = min(len(flat), start + self.size)
            text = flat[start:end].strip()
            if text:
                cid = f"{doc_id}::{i:04d}"
                out.append(ChunkRow(chunk_id=cid, doc_id=doc_id, ordinal=i, section_path=[doc_title], page=None, kind="paragraph",
                                    text=text, embed_text=text, references=extract_references(text),
                                    content_hash=content_hash(text), injection_suspected=detect_injection(text)))
                i += 1
            if end >= len(flat):
                break
            start = end - self.overlap
        for j, ch in enumerate(out):
            ch.prev_chunk_id = out[j - 1].chunk_id if j > 0 else None
            ch.next_chunk_id = out[j + 1].chunk_id if j < len(out) - 1 else None
        return out


def get_chunker(strategy: Optional[str] = None):
    strategy = strategy or get_settings().chunking_strategy
    return FixedChunker() if strategy == "fixed" else StructureChunker()
