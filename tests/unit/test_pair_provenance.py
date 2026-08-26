from ydbdoc_review.pipeline.analyze import (
    PairContent,
    PairProvenance,
    derive_pair_provenance,
    plan_pair_heuristic,
)
from ydbdoc_review.pipeline.pairs import DocPair


def test_provenance_decision_matrix():
    assert derive_pair_provenance(current_ru="ru", current_en="en") is PairProvenance.CURRENT_PAIR
    assert derive_pair_provenance(current_ru="ru", current_en=None) is PairProvenance.CURRENT_RU_MISSING_EN
    assert derive_pair_provenance(current_ru=None, current_en=None) is PairProvenance.SUPERSEDED_ABSENT


def test_current_ru_missing_en_uses_current_authority():
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    plan = plan_pair_heuristic(PairContent(pair, ru_text="historical", provenance=PairProvenance.CURRENT_RU_MISSING_EN, current_ru_text="current"))
    assert plan.action == "translate_to_en"
    assert plan.authoritative_source_text == "current"


def test_superseded_absent_skips():
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
    assert plan_pair_heuristic(PairContent(pair, ru_text="historical", provenance=PairProvenance.SUPERSEDED_ABSENT)).action == "skip"


def test_explicit_deletion_precedes_current_en_orphan_and_blocks_reused_en():
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True, ru_deleted=True)
    safe = PairContent(
        pair,
        en_text="old en",
        current_ru_text=None,
        historical_en_text="old en",
        provenance=PairProvenance.CURRENT_EN_ORPHAN,
    )
    reused = PairContent(
        pair,
        en_text="repurposed en",
        current_ru_text=None,
        historical_en_text="old en",
        provenance=PairProvenance.CURRENT_EN_ORPHAN,
    )
    assert plan_pair_heuristic(safe).action == "delete_en"
    assert plan_pair_heuristic(reused).action == "blocked"
