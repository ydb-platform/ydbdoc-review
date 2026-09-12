"""F-034 contracts for Markdown-link dependency scope."""

from __future__ import annotations

from ydbdoc_review.navigation.scope_planner import plan_translation_scope


def _readers(ru: dict[str, str], en: dict[str, str]):
    return ru.get, en.get


def test_F034_missing_target() -> None:
    seed = "ydb/docs/ru/core/full.md"
    target = "ydb/docs/ru/core/linked.md"
    read_ru, read_en = _readers(
        {
            seed: "Full document links to [the page](linked.md).\n",
            target: "The source page exists in S.\n",
        },
        {},
    )

    plan = plan_translation_scope(
        [(seed, "added")],
        read_ru=read_ru,
        read_en_base=read_en,
    )

    assert target in plan.doc_from_main


def test_F034_existing_target() -> None:
    seed = "ydb/docs/ru/core/full.md"
    target = "ydb/docs/ru/core/linked.md"
    read_ru, read_en = _readers(
        {
            seed: "Full document links to [the stale page](linked.md).\n",
            target: "The existing source text.\n",
        },
        {"ydb/docs/en/core/linked.md": "Старый перевод.\n"},
    )

    plan = plan_translation_scope(
        [(seed, "added")],
        read_ru=read_ru,
        read_en_base=read_en,
    )

    assert target not in plan.doc_from_main
