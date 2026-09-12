"""F-030: a proven rename keeps the new full target and old-path redirect."""

from __future__ import annotations

from ydbdoc_review.pipeline.analyze import PairContent, plan_pairs
from ydbdoc_review.pipeline.pairs import build_doc_pairs
from ydbdoc_review.validation.redirect_impacts import mirror_redirects_to_en


def test_F030_rename() -> None:
    """The new mirror is translated as a full file and the old URL is mirrored."""
    changes = [
        ("ydb/docs/ru/core/new/page.md", "added"),
        ("ydb/docs/ru/core/old/page.md", "deleted"),
    ]
    pairs = build_doc_pairs(changes)
    new_pair = next(pair for pair in pairs if pair.ru_path.endswith("new/page.md"))
    plan = plan_pairs(
        [
            PairContent(
                pair=new_pair,
                ru_text="## New page\n\nComplete source body.\n",
                en_text="## New page\n\nExisting target body.\n",
            )
        ]
    )[0]

    assert plan.action == "translate_to_en"
    assert plan.source_path == new_pair.ru_path
    mirrored = mirror_redirects_to_en(
        "ru:\n  - from: /old/page.md\n    to: /new/page.md\n\nen:\n",
        {"/old/page.md": "/new/page.md"},
    )
    assert "from: /old/page.md" in mirrored
    assert "to: /new/page.md" in mirrored


def test_F030_similar_files() -> None:
    """Added/deleted similar names remain separate pairs without rename proof."""
    pairs = build_doc_pairs(
        [
            ("ydb/docs/ru/core/old/page.md", "deleted"),
            ("ydb/docs/ru/core/new/page.md", "added"),
        ]
    )

    assert len(pairs) == 2
    assert {pair.ru_path for pair in pairs} == {
        "ydb/docs/ru/core/old/page.md",
        "ydb/docs/ru/core/new/page.md",
    }
    assert {pair.ru_deleted for pair in pairs} == {False, True}
