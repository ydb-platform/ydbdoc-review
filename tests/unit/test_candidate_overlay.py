from pathlib import Path

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import NavigationRunResult, PairRunResult, PRTranslationResult
from ydbdoc_review.validation.candidate_overlay import validate_candidate_overlay


def _result(path: str, text: str) -> PRTranslationResult:
    pair = DocPair(path.replace("/en/", "/ru/"), path)
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")
    return PRTranslationResult(pair_results=[PairRunResult(plan, target_text=text)])


def test_missing_markdown_target_blocks_and_anchor_is_stripped(tmp_path: Path):
    errors = validate_candidate_overlay(str(tmp_path), _result("ydb/docs/en/a.md", "[x](./missing.md#a)\n"))
    assert any("missing_markdown" in error and error.endswith("missing.md") for error in errors)


def test_pending_write_satisfies_markdown_target(tmp_path: Path):
    result = _result("ydb/docs/en/a.md", "[b](./b.md#x)\n")
    result.pair_results += _result("ydb/docs/en/b.md", "# B {#x}\n").pair_results
    assert validate_candidate_overlay(str(tmp_path), result) == []


def test_missing_toc_target_blocks(tmp_path: Path):
    result = PRTranslationResult(navigation_results=[NavigationRunResult("ydb/docs/ru/toc_i.yaml", "ydb/docs/en/toc_i.yaml", "toc", target_text="- href: missing.md\n")])
    assert any("missing_toc" in error for error in validate_candidate_overlay(str(tmp_path), result))


def test_delete_with_inbound_reference_blocks(tmp_path: Path):
    root = tmp_path / "ydb/docs/en"
    root.mkdir(parents=True)
    (root / "a.md").write_text("[old](./old.md)\n", encoding="utf-8")
    (root / "old.md").write_text("old\n", encoding="utf-8")
    pair = DocPair("ydb/docs/ru/old.md", "ydb/docs/en/old.md")
    plan = PairPlan(pair, "delete_en", pair.ru_path, pair.en_path, "ru", "en")
    result = PRTranslationResult(pair_results=[PairRunResult(plan, deleted=True)])
    assert any("delete_markdown" in error for error in validate_candidate_overlay(str(tmp_path), result))


def test_delete_is_atomic_after_all_inbound_owners_are_migrated(tmp_path: Path):
    root = tmp_path / "ydb/docs/en"
    root.mkdir(parents=True)
    (root / "a.md").write_text("[old](./old.md)\n", encoding="utf-8")
    (root / "b.md").write_text(
        "{% include [old](./old.md) %}\n", encoding="utf-8"
    )
    (root / "old.md").write_text("WIP\n", encoding="utf-8")
    result = _result("ydb/docs/en/a.md", "[feature](./feature.md)\n")
    result.pair_results += _result(
        "ydb/docs/en/b.md", "{% include [feature](./feature.md) %}\n"
    ).pair_results
    result.pair_results += _result("ydb/docs/en/feature.md", "Feature\n").pair_results
    pair = DocPair("ydb/docs/ru/old.md", "ydb/docs/en/old.md")
    plan = PairPlan(pair, "delete_en", pair.ru_path, pair.en_path, "ru", "en")
    result.pair_results.append(PairRunResult(plan, deleted=True))

    assert validate_candidate_overlay(str(tmp_path), result) == []


def test_preexisting_template_links_do_not_block_pending_writes(tmp_path: Path):
    root = tmp_path / "ydb/docs/en/core"
    root.mkdir(parents=True)
    (root / "style-guide.md").write_text("[tpl](./path/to/an/article.md)\n", encoding="utf-8")
    result = _result("ydb/docs/en/core/new-page.md", "# New\n")
    assert validate_candidate_overlay(str(tmp_path), result) == []
