from pathlib import Path

from ydbdoc_review.navigation.scope_planner import doc_pairs_from_plan, plan_translation_scope
from ydbdoc_review.pipeline.completeness import translation_pr_scope_gaps
from ydbdoc_review.validation.redirect_impacts import retarget_redirect_inbound_links


def test_F043_historic_orphan() -> None:
    seed = "ydb/docs/ru/core/current.md"
    orphan = "ydb/docs/ru/core/historic-orphan.md"

    plan = plan_translation_scope(
        [(seed, "modified")],
        read_ru={
            seed: "Current source page.\n",
            orphan: "Historical orphan.\n",
        }.get,
        read_en_base={}.get,
    )

    assert plan.doc_ru_paths == frozenset({seed})
    assert translation_pr_scope_gaps(
        doc_pairs_from_plan(plan),
        [],
        [("ydb/docs/en/core/current.md", "added")],
    ) == []


def test_F043_incoming_usage(tmp_path: Path) -> None:
    root = tmp_path / "ydb/docs/en/core"
    scoped = root / "section/source.md"
    unrelated = root / "other/unrelated.md"
    scoped.parent.mkdir(parents=True)
    unrelated.parent.mkdir(parents=True)
    scoped.write_text("See [old](../old/page.md).\n", encoding="utf-8")
    unrelated.write_text("See [old](../old/page.md).\n", encoding="utf-8")

    changed = retarget_redirect_inbound_links(
        str(tmp_path),
        {"/old/page.md": "/new/page.md"},
        allowed_paths=frozenset({scoped.relative_to(tmp_path).as_posix()}),
    )

    assert changed == [scoped.relative_to(tmp_path).as_posix()]
    assert "../new/page.md" in scoped.read_text(encoding="utf-8")
    assert "../old/page.md" in unrelated.read_text(encoding="utf-8")
