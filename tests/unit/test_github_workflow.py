"""Tests for doc_translate / doc_verify workflow."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.errors import GitHubAPIError, GitHubConfigError
from ydbdoc_review.github.workflow import (
    DocJobResult,
    _enforce_report_checkout_bytes,
    run_doc_continue,
    run_doc_translate,
    run_doc_verify,
)
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
)


def _mock_inline_verify_job() -> DocJobResult:
    return DocJobResult(
        mode="doc_verify",
        pr_number=99,
        pr_result=_fake_pr_result(),
        translation_comment_url="https://github.com/o/r/pull/99#issuecomment-verify",
    )


def _env() -> dict[str, str]:
    return {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "b1",
        "YDBDOC_YC_API_KEY": "k",
        "GITHUB_TOKEN": "gh",
        "GITHUB_PUSH_TOKEN": "ghp",
        "YDBDOC_SKIP_OPS_GATES": "1",
    }


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    ru = repo / "ydb" / "docs" / "ru"
    ru.mkdir(parents=True)
    (ru / "a.md").write_text("Привет.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def _wire_en_toc_for_a(repo_path: str) -> None:
    """Commit EN toc so ``ydb/docs/en/a.md`` is reachable (not an orphan gap)."""
    root = Path(repo_path)
    en_core = root / "ydb" / "docs" / "en" / "core"
    en_core.mkdir(parents=True, exist_ok=True)
    (en_core / "toc_p.yaml").write_text(
        "items:\n- name: A\n  href: ../a.md\n",
        encoding="utf-8",
    )
    (root / "ydb" / "docs" / "en" / "a.md").write_text("Hello.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "en toc for a.md"], cwd=repo_path, check=True)


def _fake_pr_result() -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello.\n",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    return PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text="Hello.\n", file_result=fr)]
    )


def test_report_checkout_guard_blocks_in_memory_drift():
    result = _fake_pr_result()
    with patch(
        "ydbdoc_review.github.workflow.read_text_at_ref",
        return_value="Different committed bytes.\n",
    ):
        mismatches = _enforce_report_checkout_bytes("/repo", "abc123", result)

    assert mismatches == ["ydb/docs/en/a.md"]
    file_result = result.pair_results[0].file_result
    assert file_result is not None
    assert file_result.verdict == "blocked"
    assert any(
        message.startswith("report_checkout_mismatch:")
        for message in file_result.heuristic_blocking
    )


def test_run_doc_continue_retranslates_translation_pr_scope(git_repo: str):
    pull = {
        "title": "Auto-translate docs from PR #40385",
        "head": {
            "ref": "ydbdoc-review/pr-40385",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    translated = DocJobResult(mode="doc_continue", pr_number=40385)

    from ydbdoc_review.ops.job_state import mark_continuable

    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "abc", "head": "abc"},
        translation_pr=50840,
    )

    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        mock_gh.return_value.get_pull.return_value = pull
        with patch(
            "ydbdoc_review.github.workflow.run_doc_translate",
            return_value=translated,
        ) as translate:
            with patch("ydbdoc_review.github.workflow.run_doc_verify") as verify:
                result = run_doc_continue(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=50840,
                    merge_base_with="HEAD",
                    dry_run=True,
                    config=load_config(env=_env()),
                    instruction="Переводи те файлы, которые не переведены",
                )

    assert result is translated
    translate.assert_called_once()
    assert translate.call_args.kwargs["pr_number"] == 40385
    assert translate.call_args.kwargs["continue_feedback"] == (
        "Переводи те файлы, которые не переведены"
    )
    verify.assert_not_called()


def test_run_doc_continue_verifies_non_translation_pr(git_repo: str):
    pull = {
        "title": "Critic fixup",
        "head": {
            "ref": "ydbdoc-review/verify-40385",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    verified = DocJobResult(mode="doc_continue", pr_number=50840)

    from ydbdoc_review.ops.job_state import mark_continuable

    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "abc", "head": "abc"},
        translation_pr=50840,
    )

    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        mock_gh.return_value.get_pull.return_value = pull
        with patch("ydbdoc_review.github.workflow.run_doc_translate") as translate:
            with patch(
                "ydbdoc_review.github.workflow.run_doc_verify",
                return_value=verified,
            ) as verify:
                result = run_doc_continue(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=50840,
                    merge_base_with="HEAD",
                    dry_run=True,
                    config=load_config(env=_env()),
                    instruction="Исправь замечания критика",
                )

    assert result is verified
    verify.assert_called_once()
    assert verify.call_args.kwargs["pr_number"] == 50840
    assert verify.call_args.kwargs["continue_feedback"] == "Исправь замечания критика"
    translate.assert_not_called()


def test_run_doc_continue_refuses_without_continuability_flag(git_repo: str):
    pull = {
        "title": "Auto-translate docs from PR #40385",
        "head": {
            "ref": "ydbdoc-review/pr-40385",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }

    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        mock_gh.return_value.get_pull.return_value = pull
        with patch("ydbdoc_review.github.workflow.run_doc_translate") as translate:
            with patch("ydbdoc_review.github.workflow.run_doc_verify") as verify:
                result = run_doc_continue(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=50840,
                    merge_base_with="HEAD",
                    dry_run=True,
                    config=load_config(env=_env()),
                    instruction="fix anchors",
                )

    assert result.mode == "doc_continue"
    assert result.blocked is True
    translate.assert_not_called()
    verify.assert_not_called()


def test_job_requires_nonzero_exit_when_publish_skipped():
    from ydbdoc_review.github.workflow import job_requires_nonzero_exit

    blocked_publish = DocJobResult(
        mode="doc_translate",
        pr_number=7,
        pr_result=_fake_pr_result(),
        translation_pr_number=None,
        dry_run=False,
    )
    blocked_publish.pr_result.completeness_gaps = ["ydb/docs/en/a.md"]
    assert job_requires_nonzero_exit(blocked_publish) is True

    # Translated pairs, no gaps, but no PR (e.g. push/create skipped) → fail.
    no_pr = DocJobResult(
        mode="doc_translate",
        pr_number=7,
        pr_result=_fake_pr_result(),
        translation_pr_number=None,
        dry_run=False,
    )
    assert job_requires_nonzero_exit(no_pr) is True

    bilingual = DocJobResult(
        mode="doc_translate",
        pr_number=7,
        dry_run=False,
    )
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
        en_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="skip",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    bilingual.pr_result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text=None, skipped=True)]
    )
    assert job_requires_nonzero_exit(bilingual) is False

    with_pr = DocJobResult(
        mode="doc_translate",
        pr_number=7,
        pr_result=_fake_pr_result(),
        translation_pr_number=99,
        dry_run=False,
    )
    assert job_requires_nonzero_exit(with_pr) is False

    dry = DocJobResult(
        mode="doc_translate",
        pr_number=7,
        pr_result=_fake_pr_result(),
        dry_run=True,
    )
    assert job_requires_nonzero_exit(dry) is False

    no_commit_ok = DocJobResult(
        mode="doc_translate",
        pr_number=7,
        pr_result=_fake_pr_result(),
        dry_run=False,
    )
    assert job_requires_nonzero_exit(no_commit_ok, no_commit=True) is False

    continue_blocked = DocJobResult(
        mode="doc_continue",
        pr_number=7,
        dry_run=False,
        blocked=True,
    )
    assert job_requires_nonzero_exit(continue_blocked) is True


def test_job_requires_zero_exit_verify_when_stale_blocked_verdict():
    """#52055: verify publish + all-green files must exit 0 despite stale verdict."""
    from ydbdoc_review.github.workflow import job_requires_nonzero_exit
    from ydbdoc_review.pipeline.types import NavigationRunResult

    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello.\n",
        segments_count=1,
        verdict="blocked",
        prompt_version="v1",
    )
    job = DocJobResult(
        mode="doc_verify",
        pr_number=52055,
        translation_pr_number=52055,
        pr_result=PRTranslationResult(
            pair_results=[
                PairRunResult(plan=plan, target_text="Hello.\n", file_result=fr)
            ],
            navigation_results=[
                NavigationRunResult(
                    ru_path="ydb/docs/ru/a/toc_i.yaml",
                    en_path="ydb/docs/en/a/toc_i.yaml",
                    kind="toc",
                    target_text="items:\n",
                    verdict="blocked",
                )
            ],
        ),
        dry_run=False,
    )
    assert job_requires_nonzero_exit(job) is False

def test_run_doc_translate_dry_run(git_repo: str):
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }

    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
            mock_gh.return_value.get_pull.return_value = pull
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=[("ydb/docs/ru/a.md", "modified")],
            ):
                result = run_doc_translate(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=7,
                    merge_base_with="HEAD",
                    dry_run=True,
                    config=load_config(env=_env()),
                )

    assert result.dry_run is True
    assert result.pr_result.translated_count == 1
    assert result.committed is False
    mock_gh.return_value.post_issue_comment.assert_not_called()
    assert not Path(git_repo, "ydb/docs/en/a.md").exists()


def test_run_doc_translate_merged_pr_uses_real_translation(git_repo: str):
    """Merged source PRs must translate (not critic-only verify).

    #45949 / #51696: verify planning skipped missing-EN and deleted-RU pairs.
    """
    Path(git_repo, "ydb/docs/ru/a.md").write_text("Привет, мир.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=git_repo, check=True)
    merge_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    pull = {
        "title": "historical docs",
        "head": {
            "ref": "feature/docs",
            "sha": merge_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
        "merged": True,
        "state": "closed",
        "merge_commit_sha": merge_sha,
    }

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
    ) as verify_pairs:
        with patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            return_value=_fake_pr_result(),
        ) as translate_pairs:
            with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                mock_gh.return_value.get_pull.return_value = pull
                with patch(
                    "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                    return_value=[("ydb/docs/ru/a.md", "modified")],
                ), patch(
                    "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                    return_value=[("ydb/docs/ru/a.md", "modified")],
                ):
                    result = run_doc_translate(
                        repo_path=git_repo,
                        github_repo="o/r",
                        pr_number=50741,
                        merge_base_with="HEAD",
                        dry_run=True,
                        config=load_config(env=_env()),
                    )

    assert result.pr_result.translated_count == 1
    translate_pairs.assert_called_once()
    verify_pairs.assert_not_called()


def test_run_doc_translate_missing_github_token(git_repo: str):
    env = {"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}
    with pytest.raises(GitHubConfigError):
        run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=1,
            dry_run=True,
            config=load_config(env=env),
        )


def test_run_doc_verify_dry_run(git_repo: str):
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": "source PR #3",
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "feature/docs"},
    }

    source_pull = {
        "head": {
            "sha": "source-head-sha",
            "repo": {"owner": {"login": "o"}, "name": "r"},
        }
    }

    def _get_pull(_owner: str, _repo: str, number: int) -> dict:
        if number == 11:
            return pull
        if number == 3:
            return source_pull
        raise AssertionError(f"unexpected PR {number}")

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
            mock_gh.return_value.get_pull.side_effect = _get_pull
            mock_gh.return_value.get_file_text.return_value = "RU.\n"
            mock_gh.return_value.iter_issue_comments.return_value = iter([])
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=[("ydb/docs/en/a.md", "modified")],
            ):
                result = run_doc_verify(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=11,
                    merge_base_with="HEAD",
                    dry_run=True,
                    config=load_config(env=_env()),
                )

    assert result.mode == "doc_verify"
    assert result.source_pr_number == 3
    assert result.pr_result.translated_count == 1


def test_run_doc_translate_no_pairs(git_repo: str):
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        mock_gh.return_value.get_pull.return_value = pull
        with patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("README.md", "modified")],
        ):
            result = run_doc_translate(
                repo_path=git_repo,
                github_repo="o/r",
                pr_number=7,
                dry_run=True,
                config=load_config(env=_env()),
            )
    assert result.pr_result.pair_results == []


def test_run_doc_translate_nav_only_reaches_successful_post_apply_lifecycle(
    git_repo: str,
):
    repo = Path(git_repo)
    ru_toc = repo / "ydb/docs/ru/core/toc_p.yaml"
    en_toc = repo / "ydb/docs/en/core/toc_p.yaml"
    ru_toc.parent.mkdir(parents=True, exist_ok=True)
    en_toc.parent.mkdir(parents=True, exist_ok=True)
    ru_toc.write_text("items:\n- name: A\n  href: ../a.md\n", encoding="utf-8")
    en_toc.write_text("items: []\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "navigation baseline"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    ru_toc.write_text(
        "items:\n- name: A\n  href: ../a.md\n- name: B\n  href: ../b.md\n",
        encoding="utf-8",
    )
    merged_en = "items:\n- name: A\n  href: ../a.md\n- name: B\n  href: ../b.md\n"
    nav_result = NavigationRunResult(
        ru_path="ydb/docs/ru/core/toc_p.yaml",
        en_path="ydb/docs/en/core/toc_p.yaml",
        kind="toc",
        target_text=merged_en,
        verdict="ok",
    )
    pull = {
        "title": "navigation only",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/ru/core/toc_p.yaml", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]
    ), patch(
        "ydbdoc_review.github.workflow.run_navigation_merges",
        return_value=[nav_result],
    ) as run_nav, patch(
        "ydbdoc_review.github.workflow.run_pr_translation"
    ) as run_pairs, patch(
        "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
        return_value=[],
    ):
        gh_cls.return_value.get_pull.return_value = pull
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=47856,
            merge_base_with="HEAD",
            no_commit=True,
            config=load_config(env=_env()),
        )

    run_pairs.assert_not_called()
    run_nav.assert_called_once()
    assert result.pr_result.pair_results == []
    assert result.pr_result.navigation_results == [nav_result]
    assert result.pr_result.publication_impact == "PUBLISH_NORMAL"
    assert en_toc.read_text(encoding="utf-8") == merged_en


def test_run_doc_translate_bilingual_skip_posts_source_comment(git_repo: str):
    """§6.175 / #48751: bilingual noop must still comment «перевод не требуется»."""
    from ydbdoc_review.navigation.scope_planner import TranslationScopePlan

    pull = {
        "title": "Fix glossary links",
        "merged": True,
        "merge_commit_sha": "deadbeef",
        "head": {
            "ref": "docs-glossary",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    ru = "ydb/docs/ru/core/concepts/glossary.md"
    en = "ydb/docs/en/core/concepts/glossary.md"
    changes = [(ru, "modified"), (en, "modified")]
    scope = TranslationScopePlan(
        doc_ru_paths=frozenset({ru}),
        doc_from_diff=frozenset({ru}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(),
    )
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        client = mock_gh.return_value
        client.get_pull.return_value = pull
        client.post_issue_comment.return_value = "https://github.com/o/r/pull/48751#issuecomment-1"
        with patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=changes,
        ):
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                return_value=changes,
            ):
                with patch(
                    "ydbdoc_review.github.workflow.plan_translation_scope",
                    return_value=scope,
                ):
                    with patch(
                        "ydbdoc_review.github.workflow.ensure_commit",
                        return_value=False,
                    ):
                        result = run_doc_translate(
                            repo_path=git_repo,
                            github_repo="o/r",
                            pr_number=48751,
                            dry_run=False,
                            config=load_config(env=_env()),
                        )
    assert result.translation_pr_number is None
    assert len(result.pr_result.pair_results) == 1
    assert result.pr_result.pair_results[0].skipped
    assert result.source_comment_url
    posted = client.post_issue_comment.call_args[0][3]
    assert "перевод не требуется" in posted
    assert "§6.76" in posted
    assert "bilingual" in posted.lower()


def test_run_doc_translate_posts_comments(git_repo: str):
    _wire_en_toc_for_a(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"):
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch"):
                    with patch(
                        "ydbdoc_review.github.workflow.run_doc_verify",
                        return_value=_mock_inline_verify_job(),
                    ) as mock_verify:
                        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                            mock_gh.return_value.get_pull.return_value = pull
                            mock_gh.return_value.create_pull.return_value = (
                                "https://github.com/o/r/pull/99",
                                99,
                                True,
                            )
                            mock_gh.return_value.iter_issue_comments.return_value = iter([])
                            mock_gh.return_value.post_issue_comment.return_value = "url"
                            with patch(
                                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                                return_value=[("ydb/docs/ru/a.md", "modified")],
                            ):
                                result = run_doc_translate(
                                    repo_path=git_repo,
                                    github_repo="o/r",
                                    pr_number=7,
                                    merge_base_with="HEAD",
                                    dry_run=False,
                                    config=load_config(env=_env()),
                                )

    assert result.translation_pr_number == 99
    assert result.translation_comment_url == ("https://github.com/o/r/pull/99#issuecomment-verify")
    assert result.committed is True
    assert result.pushed is True
    mock_verify.assert_called_once()
    assert mock_verify.call_args.kwargs["pr_number"] == 99
    assert mock_gh.return_value.post_issue_comment.call_count == 1
    comment_calls = mock_gh.return_value.post_issue_comment.call_args_list
    assert comment_calls[0][0][2] == 7
    mock_gh.return_value.create_pull.assert_called_once()
    mock_gh.return_value.add_issue_labels.assert_called_once_with("o", "r", 99, ["documentation"])
    _, kwargs = mock_gh.return_value.create_pull.call_args
    assert kwargs["head"] == "ydbdoc-review/pr-7"
    assert kwargs["base"] == "feature/docs"


def test_run_doc_translate_source_comment_failure_still_completes(git_repo: str):
    """Source PR comment failure must not abort after inline verify succeeded."""
    _wire_en_toc_for_a(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"):
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch"):
                    with patch(
                        "ydbdoc_review.github.workflow.run_doc_verify",
                        return_value=_mock_inline_verify_job(),
                    ):
                        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                            mock_gh.return_value.get_pull.return_value = pull
                            mock_gh.return_value.create_pull.return_value = (
                                "https://github.com/o/r/pull/99",
                                99,
                                True,
                            )
                            mock_gh.return_value.iter_issue_comments.return_value = iter([])
                            mock_gh.return_value.post_issue_comment.side_effect = GitHubAPIError(
                                "GitHub API POST .../issues/7/comments failed: HTTP 401",
                                status_code=401,
                            )
                            with patch(
                                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                                return_value=[("ydb/docs/ru/a.md", "modified")],
                            ):
                                result = run_doc_translate(
                                    repo_path=git_repo,
                                    github_repo="o/r",
                                    pr_number=7,
                                    merge_base_with="HEAD",
                                    dry_run=False,
                                    config=load_config(env=_env()),
                                )

    assert result.translation_comment_url == ("https://github.com/o/r/pull/99#issuecomment-verify")
    assert result.source_comment_url is None
    mock_gh.return_value.post_issue_comment.assert_called_once()
    assert mock_gh.return_value.post_issue_comment.call_args[0][2] == 7


def test_run_doc_translate_fork_pushes_upstream(git_repo: str):
    """Fork PR: branch from upstream main, push translation branch, PR targets main."""
    _wire_en_toc_for_a(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "parameterized-query",
            "sha": "abc",
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main"},
    }
    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prep:
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch(
                        "ydbdoc_review.github.workflow.run_doc_verify",
                        return_value=_mock_inline_verify_job(),
                    ):
                        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                            mock_gh.return_value.get_pull.return_value = pull
                            mock_gh.return_value.create_pull.return_value = (
                                "https://github.com/o/r/pull/99",
                                99,
                                True,
                            )
                            mock_gh.return_value.iter_issue_comments.return_value = iter([])
                            mock_gh.return_value.post_issue_comment.return_value = "url"
                            with patch(
                                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                                return_value=[("ydb/docs/ru/a.md", "modified")],
                            ):
                                run_doc_translate(
                                    repo_path=git_repo,
                                    github_repo="o/r",
                                    pr_number=7,
                                    merge_base_with="HEAD",
                                    dry_run=False,
                                    config=load_config(env=_env()),
                                )

    prep.assert_called_once()
    assert prep.call_args.kwargs["base_remote_url"] == "https://github.com/o/r.git"
    assert prep.call_args.kwargs["base_branch"] == "main"
    assert prep.call_args.kwargs["base_remote_name"] == "ydbdoc-review-upstream"
    push.assert_called_once()
    assert push.call_args.args[4] == "https://github.com/o/r.git"
    _, kwargs = mock_gh.return_value.create_pull.call_args
    assert kwargs["base"] == "main"
    assert kwargs["head"] == "ydbdoc-review/pr-7"


def test_run_doc_verify_fork_head_opens_fixup_pr(git_repo: str):
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "YDBDOCS-943: ...",
        "body": "",
        "head": {
            "ref": "YDBDOCS-943-feature-branch",
            "sha": "abc",
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main"},
    }

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prep:
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                        mock_gh.return_value.get_pull.return_value = pull
                        mock_gh.return_value.iter_issue_comments.return_value = iter([])
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        mock_gh.return_value.create_pull.return_value = (
                            "https://github.com/o/r/pull/99",
                            99,
                            True,
                        )
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/a.md", "modified")],
                        ):
                            result = run_doc_verify(
                                repo_path=git_repo,
                                github_repo="o/r",
                                pr_number=11,
                                merge_base_with="HEAD",
                                dry_run=False,
                                config=load_config(env=_env()),
                            )

    push.assert_called_once()
    assert push.call_args.args[2] == "ydbdoc-review/verify-11"
    assert push.call_args.args[4] == "https://github.com/o/r.git"
    prep.assert_called_once()
    assert prep.call_args.kwargs["translation_branch"] == "ydbdoc-review/verify-11"
    assert prep.call_args.kwargs["base_branch"] == "main"
    mock_gh.return_value.delete_branch.assert_called_with("o", "r", "ydbdoc-review/verify-11")
    assert mock_gh.return_value.delete_branch.call_count >= 1
    mock_gh.return_value.create_pull.assert_called_once()
    create_kwargs = mock_gh.return_value.create_pull.call_args.kwargs
    assert create_kwargs["head"] == "ydbdoc-review/verify-11"
    assert create_kwargs["base"] == "main"
    assert result.translation_pr_number == 99
    assert result.source_comment_url == "url"
    posted_bodies = [c.args[3] for c in mock_gh.return_value.post_issue_comment.call_args_list]
    assert any("#99" in body for body in posted_bodies)


def test_run_doc_verify_fork_head_resets_existing_fixup_branch(git_repo: str):
    """Second run on a fork PR: stale remote fixup branch is deleted before push."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "YDBDOCS-943: ...",
        "body": "",
        "head": {
            "ref": "YDBDOCS-943-feature-branch",
            "sha": "abc",
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main"},
    }

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"):
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                        mock_gh.return_value.get_pull.return_value = pull
                        mock_gh.return_value.iter_issue_comments.return_value = iter([])
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        # Existing fixup branch from a previous run.
                        mock_gh.return_value.delete_branch.return_value = True
                        mock_gh.return_value.create_pull.return_value = (
                            "https://github.com/o/r/pull/100",
                            100,
                            True,
                        )
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/a.md", "modified")],
                        ):
                            result = run_doc_verify(
                                repo_path=git_repo,
                                github_repo="o/r",
                                pr_number=11,
                                merge_base_with="HEAD",
                                dry_run=False,
                                config=load_config(env=_env()),
                            )

    mock_gh.return_value.delete_branch.assert_called_with("o", "r", "ydbdoc-review/verify-11")
    assert mock_gh.return_value.delete_branch.call_count >= 2
    push.assert_called_once()
    assert push.call_args.args[2] == "ydbdoc-review/verify-11"
    assert result.translation_pr_number == 100


def test_run_doc_verify_deletes_stale_fixup_branch_at_start(git_repo: str):
    """Re-run deletes ydbdoc-review/verify-N before LLM work (§6.136)."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "docs bilingual",
        "body": "",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    changes = [
        ("ydb/docs/ru/a.md", "modified"),
        ("ydb/docs/en/a.md", "modified"),
    ]

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
            mock_gh.return_value.get_pull.return_value = pull
            mock_gh.return_value.iter_issue_comments.return_value = iter([])
            mock_gh.return_value.post_issue_comment.return_value = "url"
            mock_gh.return_value.delete_branch.return_value = True
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=changes,
            ):
                with patch(
                    "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                    return_value=changes,
                ):
                    run_doc_verify(
                        repo_path=git_repo,
                        github_repo="o/r",
                        pr_number=47233,
                        merge_base_with="HEAD",
                        dry_run=False,
                        no_commit=True,
                        config=load_config(env=_env()),
                    )

    mock_gh.return_value.delete_branch.assert_called_with("o", "r", "ydbdoc-review/verify-47233")


def test_run_doc_verify_translation_pr_pushes_fixes_inline(git_repo: str):
    """Translation PR: critic fixes commit on ydbdoc-review/pr-N, no fixup PR (§6.75)."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": "abc",
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "feature/docs"},
    }

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prep:
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                        mock_gh.return_value.get_pull.return_value = pull
                        mock_gh.return_value.iter_issue_comments.return_value = iter([])
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/a.md", "modified")],
                        ):
                            result = run_doc_verify(
                                repo_path=git_repo,
                                github_repo="o/r",
                                pr_number=11,
                                merge_base_with="HEAD",
                                dry_run=False,
                                config=load_config(env=_env()),
                            )

    prep.assert_called_once()
    assert prep.call_args.kwargs["translation_branch"] == "ydbdoc-review/pr-3"
    assert prep.call_args.kwargs["base_branch"] == "ydbdoc-review/pr-3"
    push.assert_called_once()
    assert push.call_args.args[2] == "ydbdoc-review/pr-3"
    assert push.call_args.args[4] == "https://github.com/o/r.git"
    mock_gh.return_value.delete_branch.assert_not_called()
    mock_gh.return_value.create_pull.assert_not_called()
    assert result.translation_pr_number == 11
    posted_bodies = [c.args[3] for c in mock_gh.return_value.post_issue_comment.call_args_list]
    assert len(posted_bodies) == 1
    assert "коммитом в эту ветку" not in posted_bodies[0]


def test_run_doc_verify_same_repo_author_pr_opens_fixup_pr(git_repo: str):
    """Unmerged same-repo PR: never push critic fixes to the author's head branch."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "docs: feature",
        "body": "",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main"},
    }

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prep:
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                        mock_gh.return_value.get_pull.return_value = pull
                        mock_gh.return_value.iter_issue_comments.return_value = iter([])
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        mock_gh.return_value.create_pull.return_value = (
                            "https://github.com/o/r/pull/99",
                            99,
                            True,
                        )
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/a.md", "modified")],
                        ):
                            result = run_doc_verify(
                                repo_path=git_repo,
                                github_repo="o/r",
                                pr_number=7,
                                merge_base_with="HEAD",
                                dry_run=False,
                                config=load_config(env=_env()),
                            )

    assert push.call_args.args[2] == "ydbdoc-review/verify-7"
    assert push.call_args.args[2] != "feature/docs"
    assert prep.call_args.kwargs["base_branch"] == "feature/docs"
    create_kwargs = mock_gh.return_value.create_pull.call_args.kwargs
    assert create_kwargs["base"] == "main"
    assert result.translation_pr_number == 99


def test_run_doc_verify_posts_comment(git_repo: str):
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")
    content_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"], text=True
    ).strip()

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "feature/docs"},
    }

    def _fake_prepare(*_a, **_k):
        # Simulate prepare_* moving HEAD away from the verified content tip.
        subprocess.check_call(
            ["git", "-C", git_repo, "commit", "--allow-empty", "-m", "main tip"],
        )

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch(
            "ydbdoc_review.github.workflow.prepare_translation_branch_on_base",
            side_effect=_fake_prepare,
        ):
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch"):
                    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                        mock_gh.return_value.get_pull.return_value = pull
                        mock_gh.return_value.iter_issue_comments.return_value = iter(
                            [{"body": "ydbdoc-review — отчёт №1"}]
                        )
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        mock_gh.return_value.create_pull.return_value = (
                            "https://github.com/o/r/pull/99",
                            99,
                            True,
                        )
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/a.md", "modified")],
                        ):
                            result = run_doc_verify(
                                repo_path=git_repo,
                                github_repo="o/r",
                                pr_number=11,
                                merge_base_with="HEAD",
                                dry_run=False,
                                config=load_config(env=_env()),
                            )

    assert result.translation_comment_url == "url"
    assert mock_gh.return_value.post_issue_comment.call_count == 1
    posted = mock_gh.return_value.post_issue_comment.call_args.args[3]
    assert "отчёт №2" in posted
    assert "отчёт #2" not in posted
    assert f"Checkout: `{content_sha[:12]}`" in posted
    after_prepare = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"], text=True
    ).strip()
    assert after_prepare != content_sha
    assert f"Checkout: `{after_prepare[:12]}`" not in posted


def test_run_doc_verify_bilingual_source_pr_no_completeness_gaps(git_repo: str):
    """Author PR with RU+EN in the same diff: completeness OK, locales from checkout."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "YDBDOCS-2562: fix resource_weight (mentions PR #999 noise)",
        "body": "Fix typo in RU and EN.",
        "head": {
            "ref": "fix/YDBDOCS-2562-fix",
            "sha": "abc",
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main"},
        "merged": True,
        "merge_commit_sha": "mergeabc",
    }
    changes = [
        ("ydb/docs/ru/a.md", "modified"),
        ("ydb/docs/en/a.md", "modified"),
    ]

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
            mock_gh.return_value.get_pull.return_value = pull
            mock_gh.return_value.iter_issue_comments.return_value = iter([])
            mock_gh.return_value.post_issue_comment.return_value = "url"
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=changes,
            ):
                with patch(
                    "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                    return_value=changes,
                ):
                    result = run_doc_verify(
                        repo_path=git_repo,
                        github_repo="o/r",
                        pr_number=47233,
                        merge_base_with="HEAD",
                        dry_run=True,
                        config=load_config(env=_env()),
                    )

    assert result.mode == "doc_verify"
    assert result.source_pr_number is None  # not redirected to #999
    assert result.pr_result.completeness_gaps == []
    assert result.pr_result.translated_count == 1


def test_run_doc_verify_skips_glossary_disk_write(git_repo: str):
    """Verify must not commit hybridized glossary EN (#49578 / §6.189)."""
    en = Path(git_repo) / "ydb" / "docs" / "en" / "core" / "concepts"
    en.mkdir(parents=True)
    glossary = en / "glossary.md"
    good_en = "Sessions: [{#T}](query_execution/execution_process.md#sessions).\n"
    glossary.write_text(good_en, encoding="utf-8")

    pair = DocPair(
        ru_path="ydb/docs/ru/core/concepts/glossary.md",
        en_path="ydb/docs/en/core/concepts/glossary.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Сессии: кириллица.\n",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    pr_result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="Сессии: кириллица.\n",
                file_result=fr,
            )
        ]
    )

    pull = {
        "title": "Auto-translate docs from PR #45667",
        "body": "source PR #45667",
        "head": {
            "ref": "ydbdoc-review/pr-45667",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "feature/docs"},
    }

    source_pull = {
        "head": {
            "sha": "source-head-sha",
            "repo": {"owner": {"login": "o"}, "name": "r"},
        }
    }

    def _get_pull(_owner: str, _repo: str, number: int) -> dict:
        if number == 49578:
            return pull
        if number == 45667:
            return source_pull
        raise AssertionError(f"unexpected PR {number}")

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=pr_result,
    ):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"):
            with patch(
                "ydbdoc_review.github.workflow.git_commit_paths", return_value=True
            ) as commit:
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                        mock_gh.return_value.get_pull.side_effect = _get_pull
                        mock_gh.return_value.get_file_text.return_value = "RU.\n"
                        mock_gh.return_value.iter_issue_comments.return_value = iter([])
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/core/concepts/glossary.md", "modified")],
                        ):
                            run_doc_verify(
                                repo_path=git_repo,
                                github_repo="o/r",
                                pr_number=49578,
                                merge_base_with="HEAD",
                                dry_run=False,
                                config=load_config(env=_env()),
                            )

    assert glossary.read_text(encoding="utf-8") == good_en
    commit.assert_not_called()
    push.assert_not_called()


def test_run_doc_verify_bilingual_source_pr_ru_only_completeness_gap(git_repo: str):
    """Author PR that changes RU without EN mirror → completeness 🔴."""
    pull = {
        "title": "docs: RU-only tweak",
        "body": "",
        "head": {
            "ref": "docs/ru-only",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    changes = [("ydb/docs/ru/a.md", "modified")]
    empty = PRTranslationResult()

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=empty,
    ):
        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
            mock_gh.return_value.get_pull.return_value = pull
            mock_gh.return_value.iter_issue_comments.return_value = iter([])
            mock_gh.return_value.post_issue_comment.return_value = "url"
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=changes,
            ):
                with patch(
                    "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                    return_value=changes,
                ):
                    result = run_doc_verify(
                        repo_path=git_repo,
                        github_repo="o/r",
                        pr_number=42,
                        merge_base_with="HEAD",
                        dry_run=True,
                        config=load_config(env=_env()),
                    )

    assert result.source_pr_number is None
    assert result.pr_result.completeness_gaps == ["ydb/docs/en/a.md"]
