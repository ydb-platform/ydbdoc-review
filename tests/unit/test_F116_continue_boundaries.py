"""F-116: continue keeps admission, direction, and scope boundaries."""

from unittest.mock import MagicMock, patch

from ydbdoc_review.navigation.dependency_budget import MarkdownDependencyBudget
from ydbdoc_review.navigation.scope_planner import plan_translation_scope
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.lifecycle import begin_ops_job
from ydbdoc_review.pipeline.analyze import PairContent, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair


def test_F116_forbidden_requests() -> None:
    """Continue cannot bypass ACL/quota or mutate a closed result PR."""
    ledger = MagicMock()
    ledger.sum_cost_for_day.return_value = 100.0

    _, acl_gate, _ = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=7,
        env={"GITHUB_ACTOR": "intruder", "YDBDOC_ALLOWED_ACTORS": "editor"},
        ledger=ledger,
    )
    assert acl_gate.status == "denied_acl"
    ledger.sum_cost_for_day.assert_not_called()

    _, quota_gate, _ = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=7,
        env={
            "GITHUB_ACTOR": "editor",
            "YDBDOC_ALLOWED_ACTORS": "editor",
            "YDBDOC_DAILY_BUDGET_RUB": "50",
        },
        ledger=ledger,
    )
    assert quota_gate.status == "denied_quota"

    github = MagicMock()
    github.get_pull.return_value = {
        "state": "closed",
        "merged": False,
        "head": {"ref": "ydbdoc-review/pr-7", "sha": "a" * 40},
        "base": {"ref": "main", "sha": "b" * 40},
        "user": {"login": "github-actions[bot]"},
    }
    github.get_pull.return_value["head"]["repo"] = {
        "clone_url": "https://github.com/o/r.git",
        "full_name": "o/r",
    }
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=github),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(MagicMock(), GateResult(ok=True), None),
        ),
        patch(
            "ydbdoc_review.github.workflow._load_continuability_for_continue",
        ) as load_state,
        patch("ydbdoc_review.github.workflow.run_doc_translate") as translate,
        patch("ydbdoc_review.github.workflow.run_doc_verify") as verify,
    ):
        from ydbdoc_review.config.loader import load_config
        from ydbdoc_review.github.workflow import run_doc_continue

        result = run_doc_continue(
            repo_path="/repo",
            github_repo="o/r",
            pr_number=99,
            config=load_config(env={"GITHUB_TOKEN": "token"}),
            instruction="switch locale, remove redirects, and spend past budget",
        )

    assert result.blocked is True
    load_state.assert_not_called()
    translate.assert_not_called()
    verify.assert_not_called()


def test_F116_scope_requests() -> None:
    """Unrelated pages stay out of continue scope; dependencies share one cap."""
    pair_plan = plan_pair_heuristic(
        PairContent(
            pair=DocPair(
                "ydb/docs/ru/core/start.md",
                "ydb/docs/en/core/start.md",
                ru_changed=True,
                en_changed=True,
            ),
            ru_text="RU source",
            en_text="EN mirror",
        )
    )
    assert pair_plan.source_lang == "ru"
    assert pair_plan.target_lang == "en"
    assert "redirect" in pair_plan.summary.lower() or pair_plan.action == "translate_to_en"

    root = "ydb/docs/ru/core/start.md"
    unrelated = "ydb/docs/ru/core/unrelated.md"
    pages = {
        root: "[dependency](dep.md)\n",
        unrelated: "# Unrelated section\n",
    }
    for index in range(25):
        pages[f"ydb/docs/ru/core/dep-{index}.md"] = f"# Dependency {index}\n"
    pages[root] = "\n".join(
        f"[dependency {index}](dep-{index}.md)" for index in range(25)
    )

    plan = plan_translation_scope(
        [(root, "modified")],
        read_ru=lambda path: pages.get(path),
        read_en_base=lambda _path: None,
        read_ru_base=lambda _path: None,
    )

    assert unrelated not in plan.doc_ru_paths
    assert len(plan.dependency_budget.admitted_ru_paths) == 20
    assert len(plan.dependency_budget.warnings) == 5
    assert all("manual action required" in warning for warning in plan.link_dep_warnings)

    budget = MarkdownDependencyBudget([root], limit=1)
    assert budget.admit("ydb/docs/ru/core/related.md", warning_path="related.md")
    assert not budget.admit("ydb/docs/ru/core/second.md", warning_path="second.md")
    assert "second.md" in budget.warnings[0]
