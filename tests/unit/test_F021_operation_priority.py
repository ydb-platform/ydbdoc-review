"""F-021: RU priority applies to file operations as well as translation."""

from ydbdoc_review.pipeline.analyze import PairContent, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair


def _plan(**flags: bool):
    pair = DocPair(
        ru_path="docs/ru/page.md",
        en_path="docs/en/page.md",
        **flags,
    )
    return plan_pair_heuristic(
        PairContent(pair=pair, ru_text="RU source", en_text="EN mirror")
    )


def test_F021_bilingual_delete() -> None:
    ru_deleted = _plan(ru_changed=True, en_changed=True, ru_deleted=True)
    en_deleted = _plan(ru_changed=True, en_changed=True, en_deleted=True)

    assert ru_deleted.action == "delete_en"
    assert "redirect" in ru_deleted.summary
    assert en_deleted.action == "translate_to_en"
    assert en_deleted.source_lang == "ru"


def test_F021_both_and_en_only() -> None:
    both_deleted = _plan(
        ru_changed=True,
        en_changed=True,
        ru_deleted=True,
        en_deleted=True,
    )
    en_only_deleted = _plan(en_changed=True, en_deleted=True)

    assert both_deleted.action == "delete_en"
    assert en_only_deleted.action == "delete_ru"
    assert en_only_deleted.target_path.endswith("/ru/page.md")
    assert "redirect" in en_only_deleted.summary


def test_F021_ru_deletion_wins_over_translation_obligation() -> None:
    obligated_delete = _plan(translation_required=True, ru_deleted=True)

    assert obligated_delete.action == "delete_en"
    assert "redirect" in obligated_delete.summary
