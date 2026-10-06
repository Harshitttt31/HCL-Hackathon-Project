from datetime import date

import pytest
from pydantic import ValidationError

from app.sources.models import SourceMetadata, parse_supersedes
from app.sources.scope import batch_match, parse_batch_spec, programme_match, scope_match
from app.sources.temporal import is_effective, window_status

BASE = dict(doc_id="ACAD-REG-2024", title="Academic Regulations", issuer="Office of the Dean (Academics)",
            authority_level=1, doc_type="regulation", version="3.1", effective_from="2024-07-01")


def test_programme_matching_is_tristate():
    assert programme_match("ALL", None) == "yes"
    assert programme_match("B.Tech", "B.Tech CSE") == "yes"
    assert programme_match("B.Tech CSE", "B.Tech ECE") == "no"
    assert programme_match("B.Tech CSE", "B.Tech") == "unknown"  # narrower scope than the context
    assert programme_match("M.Tech; B.Tech", "B.Tech ECE") == "yes"
    assert programme_match("B.Tech", None) == "unknown"


@pytest.mark.parametrize("spec,year,expected", [
    ("ALL", 2020, "yes"), ("2023+", 2023, "yes"), ("2023+", 2022, "no"), ("2021-2023", 2022, "yes"),
    ("2021–2023", 2024, "no"), ("<=2022", 2022, "yes"), ("<2022", 2022, "no"), ("2021; 2024+", 2024, "yes"),
    ("2023+", None, "unknown"),
])
def test_batch_matching(spec, year, expected):
    assert batch_match(spec, year) == expected


def test_malformed_batch_scope_rejected():
    with pytest.raises(ValueError):
        parse_batch_spec("twenty twenty")
    with pytest.raises(ValueError):
        parse_batch_spec("2025-2021")


def test_scope_match_combines():
    assert scope_match("B.Tech", "2024+", "B.Tech CSE", 2023) == "no"
    assert scope_match("B.Tech", "2023+", "B.Tech CSE", 2023) == "yes"
    assert scope_match("B.Tech", "2023+", None, 2023) == "unknown"


def test_window_boundaries():
    as_of = date(2026, 10, 6)
    assert window_status("2026-10-07", None, as_of) == "upcoming"
    assert window_status("2026-10-06", None, as_of) == "effective"
    assert window_status("2024-01-01", "2026-10-05", as_of) == "expired"
    assert window_status("2024-01-01", "2026-10-06", as_of) == "effective"
    assert is_effective("2024-01-01", "", as_of)


def test_source_metadata_valid_and_normalised():
    m = SourceMetadata(**BASE, supersedes="ACAD-REG-2021#7.2, CIRC-1", scope_programmes="B.Tech", synthetic="yes")
    assert m.supersedes == "ACAD-REG-2021#7.2;CIRC-1" and m.synthetic == "Y" and m.retrieved_on


@pytest.mark.parametrize("patch", [
    {"authority_level": 6}, {"authority_level": "high"}, {"doc_type": "memo"}, {"effective_from": "01/07/2024"},
    {"effective_to": "2023-01-01"}, {"doc_id": "bad id"}, {"doc_id": "a#b"}, {"scope_batches": "soon"},
    {"supersedes": "###"}, {"title": "  "}, {"synthetic": "maybe"}, {"supersedes": "ACAD-REG-2024"},
])
def test_source_metadata_rejects_malformed(patch):
    with pytest.raises(ValidationError):
        SourceMetadata(**{**BASE, **patch})


def test_unknown_keys_are_not_stored():
    m = SourceMetadata(**BASE, evil_field="DROP TABLE students")
    assert not hasattr(m, "evil_field") and "evil_field" not in m.model_dump()


def test_warnings_for_odd_level_and_low_level_supersedes():
    m = SourceMetadata(**{**BASE, "doc_type": "faq", "authority_level": 1})
    assert m.warnings()
    m2 = SourceMetadata(**{**BASE, "doc_type": "notice", "authority_level": 3, "doc_id": "N1", "supersedes": "X1"})
    assert any("ignored" in w for w in m2.warnings())


def test_parse_supersedes():
    refs = parse_supersedes("DOC-A; DOC-B#7.2 ;DOC-C#4.1(a)")
    assert [(r.doc_id, r.clause) for r in refs] == [("DOC-A", None), ("DOC-B", "7.2"), ("DOC-C", "4.1(a)")]
