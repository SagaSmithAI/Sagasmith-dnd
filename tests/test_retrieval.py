from sagasmith_core.retrieval import enrich_query

from sagasmith_dnd.retrieval import DND5E_QUERY_HINTS


def test_dnd_query_hints_restore_system_vocabulary_outside_core() -> None:
    enriched = enrich_query("寻找隐藏陷阱", extra_terms=DND5E_QUERY_HINTS)

    assert "hidden" in enriched
    assert "trap" in enriched
