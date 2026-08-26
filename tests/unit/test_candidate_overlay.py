import subprocess
from pathlib import Path

from ydbdoc_review.github.workflow import _apply_transaction_gates
from ydbdoc_review.pipeline.analyze import PairPlan, PairProvenance
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


def _commit_overlay_baseline(tmp_path: Path, owner_text: str) -> tuple[str, PRTranslationResult]:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    root = tmp_path / "ydb/docs/en/core"
    root.mkdir(parents=True)
    (root / "security.md").write_text(owner_text, encoding="utf-8")
    (root / "old.md").write_text("Old target.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "pinned current base"], cwd=tmp_path, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    pair = DocPair(
        "ydb/docs/ru/core/new.md",
        "ydb/docs/en/core/new.md",
        previous_en_path="ydb/docs/en/core/old.md",
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, pair.en_path, "ru", "en")
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan,
                target_text="New target.\n",
                source_text="New target.\n",
                additional_delete_paths=("ydb/docs/en/core/old.md",),
            )
        ]
    )
    return sha, result


def test_pinned_overlay_ignores_stale_historical_worktree(tmp_path: Path):
    sha, result = _commit_overlay_baseline(tmp_path, "[new](./new.md)\n")
    security = tmp_path / "ydb/docs/en/core/security.md"
    security.write_text("[stale old](./old.md)\n", encoding="utf-8")
    stale_dynamic = tmp_path / "ydb/docs/en/core/dynamic-config.md"
    stale_dynamic.write_text("Historical tombstone bytes.\n", encoding="utf-8")

    first = validate_candidate_overlay(str(tmp_path), result, baseline_ref=sha)
    security.write_text("[different stale old](./old.md)\n", encoding="utf-8")
    stale_dynamic.write_text("Different historical bytes.\n", encoding="utf-8")
    second = validate_candidate_overlay(str(tmp_path), result, baseline_ref=sha)

    assert first == second == []


def test_pinned_overlay_attributes_one_inbound_delete_and_pending_retarget_fixes_it(
    tmp_path: Path,
):
    sha, result = _commit_overlay_baseline(tmp_path, "[old](./old.md)\n")

    issues = validate_candidate_overlay(str(tmp_path), result, baseline_ref=sha)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == "candidate_overlay_delete_markdown_reference"
    assert issue.owner_path == "ydb/docs/en/core/security.md"
    assert issue.target_path == "ydb/docs/en/core/old.md"
    assert issue.responsible_path == "ydb/docs/en/core/old.md"

    result.pair_results += _result(
        "ydb/docs/en/core/security.md", "[new](./new.md)\n"
    ).pair_results
    assert validate_candidate_overlay(str(tmp_path), result, baseline_ref=sha) == []


def test_structured_inbound_issue_blocks_move_without_blaming_tombstone(tmp_path: Path):
    sha, result = _commit_overlay_baseline(tmp_path, "[old](./old.md)\n")
    dynamic_pair = DocPair(
        "ydb/docs/ru/core/dynamic-config.md",
        "ydb/docs/en/core/dynamic-config.md",
        ru_changed=True,
    )
    dynamic_plan = PairPlan(
        dynamic_pair,
        "skip",
        dynamic_pair.ru_path,
        dynamic_pair.en_path,
        "ru",
        "en",
        provenance=PairProvenance.SUPERSEDED_ABSENT,
    )
    result.pair_results.append(
        PairRunResult(
            dynamic_plan,
            skipped=True,
            historical_disposition="superseded_absent",
        )
    )

    _apply_transaction_gates(
        str(tmp_path), result, docs_root="ydb/docs", baseline_ref=sha
    )

    assert result.completeness_states["ydb/docs/en/core/new.md"] == "blocked"
    assert (
        result.completeness_states["ydb/docs/en/core/dynamic-config.md"]
        == "superseded_absent"
    )
    assert result.completeness_gaps == [
        "candidate_overlay_delete_markdown_reference: "
        "ydb/docs/en/core/security.md -> ydb/docs/en/core/old.md"
    ]
