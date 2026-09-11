"""F-020: RU is authoritative for bilingual pairs."""

from ydbdoc_review.pipeline.analyze import PairContent, plan_from_analyze, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.schemas import AnalyzePairResult


def _content(**pair_flags: bool) -> PairContent:
    return PairContent(
        pair=DocPair(
            ru_path="docs/ru/page.md",
            en_path="docs/en/page.md",
            **pair_flags,
        ),
        ru_text="RU source",
        en_text="EN mirror",
    )


def test_F020_directions() -> None:
    ru_only = plan_pair_heuristic(_content(ru_changed=True))
    en_only = plan_pair_heuristic(_content(en_changed=True))
    both = plan_pair_heuristic(_content(ru_changed=True, en_changed=True))

    assert (ru_only.action, ru_only.source_lang, ru_only.target_lang) == (
        "translate_to_en",
        "ru",
        "en",
    )
    assert (en_only.action, en_only.source_lang, en_only.target_lang) == (
        "translate_to_ru",
        "en",
        "ru",
    )
    assert (both.action, both.source_lang, both.target_lang) == (
        "translate_to_en",
        "ru",
        "en",
    )
    assert "bilingual" not in both.summary.lower()


def test_F020_analyze_decision() -> None:
    content = _content(ru_changed=True, en_changed=True)
    aligned = plan_from_analyze(
        content,
        AnalyzePairResult(
            ru_path=content.pair.ru_path,
            en_path=content.pair.en_path,
            ru_present=True,
            en_present=True,
            semantically_aligned=True,
            needs_generation_for=None,
            summary="already aligned",
        ),
    )
    stale = plan_from_analyze(
        content,
        AnalyzePairResult(
            ru_path=content.pair.ru_path,
            en_path=content.pair.en_path,
            ru_present=True,
            en_present=True,
            semantically_aligned=False,
            needs_generation_for="en",
            summary="EN is stale",
        ),
    )

    assert aligned.action == "critic_only"
    assert stale.action == "translate_to_en"
    assert stale.source_path == content.pair.ru_path
    assert "bilingual" not in stale.summary.lower()
