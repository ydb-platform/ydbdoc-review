"""F-106: doc_verify validates navigation and only repairs exact link targets."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.pr import PullRequestContext
from ydbdoc_review.github.workflow import _run_verify_pairs, run_doc_verify
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair
from ydbdoc_review.pipeline.types import NavigationRunResult
from ydbdoc_review.translation.glossary import load_glossary


def test_F106_read_navigation() -> None:
    pair = NavigationPair(
        ru_path="ydb/docs/ru/topic/toc.yaml",
        en_path="ydb/docs/en/topic/toc.yaml",
        ru_changed=True,
    )
    navigation = NavigationRunResult(
        ru_path=pair.ru_path,
        en_path=pair.en_path,
        kind="toc",
        verdict="ok",
    )
    context = PullRequestContext(
        owner="owner",
        repo="repo",
        number=106,
        title="Docs",
        head_ref="feature/docs",
        head_sha="candidate-sha",
        head_repo_full_name="owner/repo",
        head_repo_https_url="https://github.com/owner/repo.git",
        base_ref="main",
    )
    github = MagicMock()
    github.get_branch_sha.return_value = None
    github.iter_issue_comments.return_value = iter([])
    github.post_issue_comment.return_value = "report-url"

    def block_after_read_only_verify(result, **_kwargs) -> None:
        result.navigation_results[0].warnings.append(
            "missing_toc_target: `topic.md` does not exist"
        )
        result.navigation_results[0].verdict = "blocked"

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=github),
        patch("ydbdoc_review.github.workflow.pull_request_context", return_value=context),
        patch("ydbdoc_review.github.workflow.resolve_commit_ref", return_value="candidate-sha"),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_git", return_value=[]),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]),
        patch("ydbdoc_review.github.workflow.build_pairs_from_changes", return_value=[]),
        patch(
            "ydbdoc_review.github.workflow.build_verify_navigation_pairs",
            return_value=[pair],
        ),
        patch(
            "ydbdoc_review.github.workflow.build_en_toc_reachable_from_repo",
            return_value=frozenset(),
        ),
        patch(
            "ydbdoc_review.github.workflow.run_navigation_verifies",
            return_value=[navigation],
        ),
        patch(
            "ydbdoc_review.github.workflow.apply_toc_target_checks",
            side_effect=block_after_read_only_verify,
        ),
        patch("ydbdoc_review.github.workflow.read_text_at_commit", return_value=None),
        patch("ydbdoc_review.github.workflow.completeness_gaps", return_value=[]),
    ):
        job = run_doc_verify(
            repo_path="/unused",
            github_repo="owner/repo",
            pr_number=106,
            merge_base_with="HEAD",
            config=load_config(
                env={
                    "YDBDOC_YC_FOLDER_ID": "folder",
                    "YDBDOC_YC_API_KEY": "key",
                    "GITHUB_TOKEN": "token",
                    "GITHUB_PUSH_TOKEN": "push-token",
                }
            ),
            skip_ops_gates=True,
        )

    result = job.pr_result.navigation_results[0]
    assert result.verdict == "blocked"
    assert result.target_text is None
    recommendation = next(
        warning
        for warning in result.warnings
        if warning.startswith("verify_navigation_boundary:")
    )
    assert "/ydbdoc continue" in recommendation
    assert "manually" in recommendation
    assert "doc_verify" in recommendation


def test_F106_exact_link() -> None:
    en_page = "ydb/docs/en/topic/page.md"
    ru_page = en_page.replace("/docs/en/", "/docs/ru/", 1)
    pair = DocPair(ru_path=ru_page, en_path=en_page, ru_changed=True)
    localized = "[Topic](target.md#раздел)\n"
    files = {
        "ydb/docs/en/topic/target.md": "## Section {#section}\n",
        "ydb/docs/ru/topic/target.md": "## Раздел\n",
        "ydb/docs/en/topic/related.md": "## Related section {#section}\n",
    }
    reader = MagicMock(side_effect=files.get)
    client = MagicMock(spec=YandexLLMClient)
    client.usage_tracker.records = []
    config = load_config(
        env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"}
    )

    def preserve(
        en_baseline: str,
        *,
        allow_retarget: bool,
        existing_target: str = "[Topic](target.md#missing)\n",
    ) -> str | None:
        content = PairContent(
            pair=pair,
            ru_text=localized,
            en_text=existing_target,
            ru_base_text="[Topic](target.md#old)\n",
            en_base_text=en_baseline,
        )
        with patch(
            "ydbdoc_review.harness.pair.apply_localized_mirror_delta",
            return_value=localized,
        ):
            result = _run_verify_pairs(
                [content],
                client,
                load_glossary(),
                config,
                docs_text_reader=reader,
                allow_navigation_retarget=allow_retarget,
            )
        return result.pair_results[0].target_text

    assert preserve(
        "[Topic](target.md#section)\n", allow_retarget=False
    ) == (
        "[Topic](target.md#section)\n"
    )
    files.pop("ydb/docs/ru/topic/target.md")
    assert preserve(
        "[Topic](related.md#section)\n",
        allow_retarget=False,
        existing_target="[Topic](related.md#section)\n",
    ) == localized
    assert preserve(
        "[Topic](related.md#section)\n",
        allow_retarget=True,
        existing_target="[Topic](related.md#section)\n",
    ) == (
        "[Topic](related.md#section)\n"
    )
