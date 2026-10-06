"""Shared text utilities: tokenisation, stemming, stop words, synonyms, IDF-weighted term coverage."""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from typing import Iterable, Optional

try:
    import snowballstemmer

    _stemmer = snowballstemmer.stemmer("english")

    def _stem(word: str) -> str:
        return _stemmer.stemWord(word)
except ImportError:  # pragma: no cover - fallback keeps the system importable
    def _stem(word: str) -> str:
        for suf in ("ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "ly", "s"):
            if word.endswith(suf) and len(word) - len(suf) >= 3:
                return word[: -len(suf)]
        return word

STOPWORDS = frozenset("""
a an the and or but if then than so of to in on at by for with from as is are was were be been being am do does did done
have has had having i me my mine we our you your he she it its they them their this that these those there here
what which who whom whose when where why how can could should would will shall may might must not no nor
about into over under after before between during out up down off again further once also very just any all each
both few more most other some such only own same too s t don now
tell please give explain describe regarding want need know get
say says said saying enough correct true right valid really sure pls plz
kitna kitni kitne hai hain kya ka ki ke batao bataye batayiye hota hoti mera meri mujhe
""".split())

# Words that name the audience (scope), not the content being asked about. Scope is applied by the precedence engine.
SCOPE_TERMS = frozenset("btech mtech bsc msc cse ece mba bca programme program batch student".split())

# Domain-neutral spelling/term variants so "supply exam" meets "supplementary examination".
SYNONYMS: dict[str, list[str]] = {
    "supply": ["supplementary"], "supplementary": ["supply", "re-examination", "reexam"], "reexam": ["supplementary"],
    "exam": ["examination"], "examination": ["exam"], "sem": ["semester"], "endsem": ["end-semester", "semester"],
    "fee": ["fees", "payment"], "fees": ["fee"], "backlog": ["arrear", "failed"], "arrear": ["backlog"],
    "cgpa": ["grade point", "gpa"], "gpa": ["cgpa"], "deadline": ["last date", "due date"], "attendance": ["presence"],
    "condone": ["condonation"], "condonation": ["condone"], "hostel": ["accommodation"], "scholarship": ["stipend", "fellowship"],
    "regulation": ["ordinance", "rules"], "circular": ["notification", "notice"], "placement": ["recruitment", "campus"],
}


@lru_cache(maxsize=20000)
def stem(word: str) -> str:
    return _stem(word.lower())


GENERIC_HEADING_TERMS = frozenset(stem(w) for w in
                                  "general overview introduction scope eligibility date last fee fees how apply application timing timings time times hours schedule".split())


def _build_synonym_index() -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for key, syns in SYNONYMS.items():
        k = stem(key)
        for syn in syns:
            for w in raw_words(syn):
                index.setdefault(k, set()).add(stem(w))
                index.setdefault(stem(w), set()).add(k)  # symmetric
    return index


_SYN_INDEX: dict[str, set[str]] = {}


def synonym_variants(token: str) -> set[str]:
    """The token plus its stemmed synonyms (symmetric)."""
    if not _SYN_INDEX:
        _SYN_INDEX.update(_build_synonym_index())
    return {token, *_SYN_INDEX.get(token, set())}


def raw_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[.'][a-z0-9]+)*", (text or "").lower().replace("’", "'"))


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    """Lower-case, split, drop stop words, stem. Numbers are kept (they carry thresholds and dates)."""
    out: list[str] = []
    for w in raw_words(text):
        w = w.replace("'", "").replace(".", "") if re.fullmatch(r"[a-z]+(?:[.'][a-z]+)+", w) else w
        if not keep_stopwords and w in STOPWORDS:
            continue
        out.append(stem(w) if not w.isdigit() else w)
    return out


def expand_terms(tokens: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        for variant in sorted(synonym_variants(t), key=lambda x: (x != t, x)):
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


class IdfTable:
    """Document-frequency statistics over the chunk corpus, used for weighted query-term coverage."""

    def __init__(self, docs_tokens: list[list[str]]):
        self.n = max(1, len(docs_tokens))
        self.df: Counter = Counter()
        for toks in docs_tokens:
            self.df.update(set(toks))
        self.max_idf = math.log(1 + (self.n + 0.5) / 0.5)

    def idf(self, token: str) -> float:
        d = self.df.get(token, 0)
        if d == 0:
            return self.max_idf  # unseen term: maximally informative (and certainly unsupported)
        return math.log(1 + (self.n - d + 0.5) / (d + 0.5))

    def coverage(self, query_tokens: list[str], evidence_tokens: set[str]) -> tuple[float, list[str]]:
        """IDF-weighted fraction of query terms found in the evidence, plus the uncovered terms.

        A term is covered if it, or one of its synonyms, appears in the evidence.
        Terms that appear nowhere in the corpus count with maximum weight, so out-of-domain questions
        ("scholarship in Antarctica") score low even though one word matches.
        """
        terms = list(dict.fromkeys(query_tokens))
        if not terms:
            return 1.0, []
        total = covered = 0.0
        missing: list[str] = []
        for t in terms:
            w = max(self.idf(t), 0.05)
            total += w
            variants = synonym_variants(t)
            if variants & evidence_tokens:
                covered += w
            else:
                missing.append(t)
        return (covered / total if total else 1.0), missing
