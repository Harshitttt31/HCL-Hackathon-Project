from app.agent.handlers import conflict_notes, upcoming_notes


def test_repeated_conflicts_produce_one_note():
    c = {"kind": "supersession", "winner": {"doc_id": "NEW", "section": "1", "effective_from": "2026-08-01", "value": "80%"},
         "overridden": [{"doc_id": "OLD", "section": "2", "value": "75%"}]}
    once = conflict_notes([c])
    assert conflict_notes([c, dict(c)]) == once


def test_repeated_upcoming_changes_produce_one_note():
    u = {"doc_id": "NEW", "section": "1", "value": "80%", "effective_from": "2026-08-01"}
    assert upcoming_notes([u, dict(u)]) == upcoming_notes([u])
