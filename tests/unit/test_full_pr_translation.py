"""Regression tests for the deliberately simple doc_translate contract."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.github.workflow import _expand_missing_markdown_dependencies
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.profiles import TRANSLATE_WITH_QA_PROFILE
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan, PairProvenance
from ydbdoc_review.pipeline.pairs import DocPair


def _context() -> HarnessContext:
    client = MagicMock()
    client.usage_tracker.records = []
    return HarnessContext.from_options(client)


def test_pr_ru_snapshot_overwrites_newer_en_without_differential_seed():
    pair = DocPair(
        "ydb/docs/ru/article.md",
        "ydb/docs/en/article.md",
        ru_changed=True,
    )
    source = """# Статья

Текст из PR.

```yaml
key: value
```
"""
    content = PairContent(
        pair=pair,
        ru_base_text="Старая RU версия.\n",
        ru_text=source,
        current_ru_text="Более новая RU версия в main.\n",
        en_base_text="Old EN.\n",
        en_text="Newer EN already in main.\n",
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")
    translated = """# Article

Text from the PR.

```yaml
key: value
```
"""

    with patch("ydbdoc_review.harness.pair.FileHarness") as harness_cls:
        harness_cls.return_value.run.return_value = SimpleNamespace(
            final_text=translated,
            differential_meta={},
        )
        result = run_pair_plan(
            content,
            plan,
            _context(),
            {},
            historical_merged_provenance=True,
        )

    state = harness_cls.return_value.run.call_args.args[0]
    assert harness_cls.call_args.args[0] is TRANSLATE_WITH_QA_PROFILE
    assert state.source_text == source
    assert state.existing_target_text is None
    assert state.base_target_text is None
    assert state.base_source_text is None
    assert result.target_text == translated
    assert "```yaml\nkey: value\n```" in result.target_text


def test_pr_ru_snapshot_translates_when_en_is_missing_from_main():
    pair = DocPair(
        "ydb/docs/ru/new.md",
        "ydb/docs/en/new.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="Новая статья из PR.\n",
        current_ru_text="Более новая статья.\n",
        en_text=None,
        provenance=PairProvenance.CURRENT_RU_MISSING_EN,
    )
    plan = PairPlan(
        pair,
        "translate_to_en",
        pair.ru_path,
        pair.en_path,
        "ru",
        "en",
        provenance=PairProvenance.CURRENT_RU_MISSING_EN,
    )

    with patch("ydbdoc_review.harness.pair.FileHarness") as harness_cls:
        harness_cls.return_value.run.return_value = SimpleNamespace(
            final_text="New article from the PR.\n",
            differential_meta={},
        )
        result = run_pair_plan(content, plan, _context(), {})

    state = harness_cls.return_value.run.call_args.args[0]
    assert state.source_text == "Новая статья из PR.\n"
    assert state.existing_target_text is None
    assert result.target_text == "New article from the PR.\n"


def test_translation_failure_is_not_silently_replaced_with_existing_en():
    pair = DocPair(
        "ydb/docs/ru/article.md",
        "ydb/docs/en/article.md",
        ru_changed=True,
    )
    content = PairContent(pair=pair, ru_text="Текст.\n", en_text="Existing EN.\n")
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")

    with patch("ydbdoc_review.harness.pair.FileHarness") as harness_cls:
        harness_cls.return_value.run.side_effect = ValueError("missing placeholder")
        result = run_pair_plan(content, plan, _context(), {})

    assert result.error == "missing placeholder"
    assert result.target_text is None


def test_en_snapshot_fully_translates_to_missing_ru_without_seed():
    pair = DocPair(
        "ydb/docs/ru/article.md", "ydb/docs/en/article.md", en_changed=True
    )
    content = PairContent(pair=pair, en_text="English PR snapshot.\n")
    plan = PairPlan(pair, "translate_to_ru", pair.en_path, pair.ru_path, "en", "ru")
    with patch("ydbdoc_review.harness.pair.FileHarness") as harness_cls:
        harness_cls.return_value.run.return_value = SimpleNamespace(
            final_text="Снимок PR на английском.\n", differential_meta={}
        )
        result = run_pair_plan(content, plan, _context(), {})
    state = harness_cls.return_value.run.call_args.args[0]
    assert state.source_text == "English PR snapshot.\n"
    assert state.existing_target_text is None
    assert state.base_source_text is None
    assert state.base_target_text is None
    assert result.target_text == "Снимок PR на английском.\n"


def test_source_deletions_remove_opposite_locale_symmetrically():
    ru_deleted = DocPair("d/ru/a.md", "d/en/a.md", ru_changed=True, ru_deleted=True)
    en_deleted = DocPair("d/ru/b.md", "d/en/b.md", en_changed=True, en_deleted=True)
    from ydbdoc_review.pipeline.analyze import plan_pair_heuristic

    ru_plan = plan_pair_heuristic(PairContent(pair=ru_deleted))
    en_plan = plan_pair_heuristic(PairContent(pair=en_deleted))
    assert ru_plan.action == "delete_en"
    assert ru_plan.target_path == ru_deleted.en_path
    assert en_plan.action == "delete_target"
    assert en_plan.target_path == en_deleted.ru_path
    assert run_pair_plan(PairContent(pair=ru_deleted), ru_plan, _context(), {}).deleted
    assert run_pair_plan(PairContent(pair=en_deleted), en_plan, _context(), {}).deleted


def test_dependency_closure_follows_missing_include_and_link_recursively():
    pair = DocPair("d/ru/a.md", "d/en/a.md", ru_changed=True)
    files = {
        ("snap", "d/ru/a.md"): "{% include [B](b.md) %}\n[x](c.md)\n",
        ("snap", "d/ru/b.md"): "[nested](nested/d.md)\n",
        ("snap", "d/ru/c.md"): "done\n",
        ("snap", "d/ru/nested/d.md"): "done\n",
        ("main", "d/en/c.md"): "already translated\n",
    }
    with patch(
        "ydbdoc_review.github.workflow.read_text_at_ref",
        side_effect=lambda _repo, ref, path: files.get((ref, path)),
    ):
        deps, chains = _expand_missing_markdown_dependencies(
            "/repo",
            source_ref="snap",
            target_ref="main",
            source_pairs=[pair],
            docs_root="d",
        )
    assert [dep.ru_path for dep in deps] == ["d/ru/b.md", "d/ru/nested/d.md"]
    assert all(dep.dependency for dep in deps)
    assert chains["d/ru/nested/d.md"] == (
        "d/ru/a.md",
        "d/ru/b.md",
        "d/ru/nested/d.md",
    )


def test_dependency_closure_blocks_over_limit_before_translation():
    pair = DocPair("d/ru/a.md", "d/en/a.md", ru_changed=True)
    links = "\n".join(f"[x](dep{i}.md)" for i in range(21))

    def read(_repo, ref, path):
        if ref == "snap" and path == "d/ru/a.md":
            return links
        if ref == "snap" and path.startswith("d/ru/dep"):
            return "dependency\n"
        return None

    with patch("ydbdoc_review.github.workflow.read_text_at_ref", side_effect=read):
        import pytest

        with pytest.raises(RuntimeError, match=r"found 21 files, limit 20"):
            _expand_missing_markdown_dependencies(
                "/repo",
                source_ref="snap",
                target_ref="main",
                source_pairs=[pair],
                docs_root="d",
            )
