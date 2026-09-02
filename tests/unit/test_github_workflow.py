"""Tests for doc_translate / doc_verify workflow."""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github import workflow
from ydbdoc_review.github.errors import GitHubAPIError, GitHubConfigError
from ydbdoc_review.github.workflow import (
    DocJobResult,
    _enforce_report_checkout_bytes,
    run_doc_continue,
    run_doc_translate,
    run_doc_verify,
)
from ydbdoc_review.ops.lifecycle import begin_ops_job as begin_ops_job_with_backends
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.provenance import ProvenanceFinding
from ydbdoc_review.pipeline.types import FileTranslationResult, PairRunResult, PRTranslationResult


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


def _git_head(repo: str) -> str:
    return subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True
    ).strip()


@pytest.fixture
def ops_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit local isolation for tests that do not exercise lifecycle gates."""
    monkeypatch.setenv("YDBDOC_SKIP_OPS_GATES", "1")


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    ru = repo / "ydb" / "docs" / "ru" / "core"
    ru.mkdir(parents=True)
    (ru / "a.md").write_text("Привет.\n", encoding="utf-8")
    en = repo / "ydb" / "docs" / "en" / "core"
    en.mkdir(parents=True, exist_ok=True)
    (en / "toc_p.yaml").write_text(
        "items:\n- name: A\n  href: a.md\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
    return str(repo)


def _fake_pr_result() -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/core/a.md",
        en_path="ydb/docs/en/core/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_ru_to_en_once",
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

    assert mismatches == ["ydb/docs/en/core/a.md"]
    file_result = result.pair_results[0].file_result
    assert file_result is not None
    assert file_result.verdict == "blocked"
    assert any(
        message.startswith("report_checkout_mismatch:")
        for message in file_result.heuristic_blocking
    )


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_continue_retranslates_translation_pr_scope(git_repo: str):
    pull = {
        "title": "Auto-translate docs from PR #40385",
        "head": {
            "ref": "ydbdoc-review/pr-40385",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
    }
    translated = DocJobResult(mode="doc_continue", pr_number=40385)

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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_continue_verifies_non_translation_pr(git_repo: str):
    pull = {
        "title": "Critic fixup",
        "head": {
            "ref": "ydbdoc-review/verify-40385",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
    }
    verified = DocJobResult(mode="doc_continue", pr_number=50840)

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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_translate_dry_run(git_repo: str):
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
    }

    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
            mock_gh.return_value.get_pull.return_value = pull
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=[("ydb/docs/ru/core/a.md", "modified")],
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
    assert not Path(git_repo, "ydb/docs/en/core/a.md").exists()


@pytest.mark.usefixtures("ops_isolation")
def test_translate_workflow_forwards_real_toc_reachability_to_transaction(
    git_repo: str,
) -> None:
    """The production workflow forwards one derived object to final validators."""
    pull = {
        "title": "docs",
        "head": {"ref": "feature/docs", "sha": _git_head(git_repo), "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"}},
        "base": {"ref": "main", "sha": _git_head(git_repo)},
    }
    sentinel = frozenset({"ydb/docs/en/sentinel.md"})
    observed: list[tuple[str, frozenset[str] | None]] = []
    roles: list[str] = []

    class DeterministicClient:
        def chat_once(self, messages, *, role, **kwargs):
            roles.append(role)
            if role == "critic":
                return SimpleNamespace(content=json.dumps({"findings": []}))
            if role == "translate":
                payload = json.loads(messages[-1]["content"])
                segments = [
                    {
                        "id": item["id"],
                        "text": item["text"].replace("Привет", "Hello"),
                    }
                    for item in payload["segments"]
                ]
                return SimpleNamespace(content=json.dumps({"segments": segments}))
            raise AssertionError(f"unexpected model role: {role}")

    def md_spy(*args, en_toc_reachable=None, **kwargs):
        observed.append(("md", en_toc_reachable))
        return []

    def href_spy(*args, en_toc_reachable=None, **kwargs):
        observed.append(("href", en_toc_reachable))
        return []

    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/ru/core/a.md", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.build_en_toc_reachable_from_repo",
        return_value=sentinel,
    ) as build_toc, patch(
        "ydbdoc_review.github.workflow.create_llm_client",
        return_value=DeterministicClient(),
    ), patch(
        "ydbdoc_review.pipeline.translation_transaction.check_md_link_parity",
        side_effect=md_spy,
    ), patch(
        "ydbdoc_review.pipeline.translation_transaction.check_href_parity",
        side_effect=href_spy,
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
        )

    build_toc.assert_called_once()
    assert [name for name, _value in observed] == ["md", "href"]
    assert all(value is sentinel for _name, value in observed)
    assert "translate" in roles
    assert "critic" in roles
    assert result.pr_result.translated_count == 1
    assert result.pr_result.failed_count == 0
    source = inspect.getsource(
        test_translate_workflow_forwards_real_toc_reachability_to_transaction
    )
    assert "run_doc_translate(" in source
    forbidden = (
        "run_" + "pr_translation",
        "run_translation_" + "transaction",
        "translate_ru_" + "to_en_once",
    )
    assert all(name not in source for name in forbidden)


@pytest.mark.usefixtures("ops_isolation")
def test_translate_workflow_queues_root_locale_page_with_exact_en_counterpart(
    git_repo: str,
) -> None:
    """A locale-root page is queued, guarded, and published atomically."""
    ru_path = "ydb/docs/ru/root-page.md"
    en_path = "ydb/docs/en/root-page.md"
    translated_text = "Translated root page.\n"
    Path(git_repo, ru_path).write_text("Корневая страница.\n", encoding="utf-8")
    Path(git_repo, en_path).write_text("Old root page.\n", encoding="utf-8")
    Path(git_repo, "ydb/docs/en/core/toc_p.yaml").write_text(
        "items:\n- name: A\n  href: a.md\n- name: Root\n  href: ../root-page.md\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "root page"], cwd=git_repo, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=git_repo, check=True)
    sha = _git_head(git_repo)
    pull = {
        "title": "root docs",
        "head": {"ref": "feature/root", "sha": sha, "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"}},
        "base": {"ref": "main", "sha": sha},
    }

    def root_result() -> PRTranslationResult:
        pair = DocPair(ru_path=ru_path, en_path=en_path, ru_changed=True)
        plan = PairPlan(
            pair=pair,
            action="translate_ru_to_en_once",
            source_path=ru_path,
            target_path=en_path,
            source_lang="ru",
            target_lang="en",
        )
        file_result = FileTranslationResult(
            file_path=en_path,
            final_text=translated_text,
            segments_count=1,
            verdict="ok",
            prompt_version="v1",
        )
        return PRTranslationResult(
            pair_results=[
                PairRunResult(
                    plan=plan,
                    target_text=translated_text,
                    file_result=file_result,
                )
            ]
        )

    successful_result = root_result()
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[(ru_path, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.run_pr_translation",
        return_value=successful_result,
    ) as translate, patch(
        "ydbdoc_review.github.workflow.guard_publication_provenance",
        wraps=workflow.guard_publication_provenance,
    ) as provenance_guard, patch(
        "ydbdoc_review.github.workflow._apply_results_to_disk",
        wraps=workflow._apply_results_to_disk,
    ) as apply_results:
        mock_gh.return_value.get_pull.return_value = pull
        workflow_calls = Mock()
        workflow_calls.attach_mock(provenance_guard, "provenance_guard")
        workflow_calls.attach_mock(translate, "translate")
        workflow_calls.attach_mock(apply_results, "apply_results")
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=8,
            merge_base_with="HEAD",
            dry_run=False,
            no_commit=True,
            config=load_config(env=_env()),
        )

    dependency_plan = translate.call_args.kwargs["dependency_plan"]
    assert [(entry.ru_path, entry.origin) for entry in dependency_plan.entries] == [
        ("ydb/docs/ru/root-page.md", "initial")
    ]
    contents = translate.call_args.args[0]
    assert [(content.pair.ru_path, content.pair.en_path) for content in contents] == [
        ("ydb/docs/ru/root-page.md", "ydb/docs/en/root-page.md")
    ]
    assert result.pr_result is successful_result
    assert [
        (run.plan.pair.ru_path, run.plan.pair.en_path)
        for run in result.pr_result.pair_results
    ] == [("ydb/docs/ru/root-page.md", "ydb/docs/en/root-page.md")]
    assert provenance_guard.call_count == 2
    for guard_call in provenance_guard.call_args_list:
        assert guard_call.kwargs["initial_ru_paths"] == {
            "ydb/docs/ru/root-page.md"
        }
        assert (
            guard_call.kwargs["to_en_path"]("ydb/docs/ru/root-page.md")
            == "ydb/docs/en/root-page.md"
        )
    assert [entry[0] for entry in workflow_calls.mock_calls] == [
        "provenance_guard",
        "translate",
        "provenance_guard",
        "apply_results",
    ]
    apply_results.assert_called_once_with(
        git_repo,
        successful_result,
        dry_run=False,
        docs_root="ydb/docs",
    )
    assert Path(git_repo, en_path).read_bytes() == translated_text.encode()

    Path(git_repo, en_path).unlink()
    finding = ProvenanceFinding(
        category="stale_source_or_newer_translation",
        reason="newer_en",
        ru_path=ru_path,
        en_path=en_path,
        baseline_ru_oid=None,
        current_ru_oid=None,
        baseline_en_oid=None,
        current_en_oid=None,
        touching_commits=(),
    )
    failed_translation = root_result()
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[(ru_path, "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.run_pr_translation",
        return_value=failed_translation,
    ), patch(
        "ydbdoc_review.github.workflow.guard_publication_provenance",
        side_effect=[(), (finding,)],
    ) as failed_guard, patch(
        "ydbdoc_review.github.workflow._apply_results_to_disk",
        wraps=workflow._apply_results_to_disk,
    ) as failed_apply:
        mock_gh.return_value.get_pull.return_value = pull
        failed_result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=8,
            merge_base_with="HEAD",
            dry_run=False,
            no_commit=True,
            config=load_config(env=_env()),
        )

    assert failed_guard.call_count == 2
    failed_apply.assert_not_called()
    assert not Path(git_repo, en_path).exists()
    assert failed_result.pr_result.provenance_findings == [finding]
    assert failed_result.pr_result.completeness_gaps == [
        "ydb/docs/en/root-page.md"
    ]


def test_run_doc_translate_exercises_in_memory_ops_lifecycle(git_repo: str):
    """The workflow starts and finalizes a real in-memory ops record per result."""
    target = Path(git_repo, "ydb/docs/en/core/a.md")
    target.write_text("Hello.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add target"], cwd=git_repo, check=True)
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
    }
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()

    def begin_with_memory(**kwargs: object):
        return begin_ops_job_with_backends(
            **kwargs,
            env={"YDBDOC_TRANSCRIPT_BACKEND": "memory"},
            ledger=ledger,
            store=store,
        )

    failed = _fake_pr_result()
    failed.pair_results[0].error = "forced failure"
    with patch(
        "ydbdoc_review.github.workflow.begin_ops_job", side_effect=begin_with_memory
    ), patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/ru/core/a.md", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.run_pr_translation",
        side_effect=[_fake_pr_result(), failed],
    ):
        mock_gh.return_value.get_pull.return_value = pull
        for number in (7, 8):
            run_doc_translate(
                repo_path=git_repo,
                github_repo="o/r",
                pr_number=number,
                merge_base_with="HEAD",
                dry_run=False,
                no_commit=True,
                config=load_config(env=_env()),
            )

    records = sorted(ledger.records, key=lambda record: record.source_pr)
    assert [(record.source_pr, record.status, record.cost_rub) for record in records] == [
        (7, "ok", 0.0),
        (8, "failed", 0.0),
    ]
    assert all(record.finished_at is not None for record in records)


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_translate_merged_pr_uses_real_translation(git_repo: str):
    """Merged source PRs must translate (not critic-only verify).

    #45949 / #51696: verify planning skipped missing-EN and deleted-RU pairs.
    """
    Path(git_repo, "ydb/docs/ru/core/a.md").write_text("Привет, мир.\n", encoding="utf-8")
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
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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
                    return_value=[("ydb/docs/ru/core/a.md", "modified")],
                ), patch(
                    "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                    return_value=[("ydb/docs/ru/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_dry_run(git_repo: str):
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": "source PR #3",
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": _git_head(git_repo),
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
                return_value=[("ydb/docs/en/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_translate_no_pairs(git_repo: str):
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_translate_posts_comments(git_repo: str):
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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
                                return_value=[("ydb/docs/ru/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_translate_source_comment_failure_still_completes(git_repo: str):
    """Source PR comment failure must not abort after inline verify succeeded."""
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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
                                return_value=[("ydb/docs/ru/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_translate_fork_pushes_upstream(git_repo: str):
    """Fork PR: branch from upstream main, push translation branch, PR targets main."""
    pull = {
        "title": "docs",
        "head": {
            "ref": "parameterized-query",
            "sha": _git_head(git_repo),
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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
                                return_value=[("ydb/docs/ru/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_fork_head_opens_fixup_pr(git_repo: str):
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "YDBDOCS-943: ...",
        "body": "",
        "head": {
            "ref": "YDBDOCS-943-feature-branch",
            "sha": _git_head(git_repo),
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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
                            return_value=[("ydb/docs/en/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_fork_head_resets_existing_fixup_branch(git_repo: str):
    """Second run on a fork PR: stale remote fixup branch is deleted before push."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "YDBDOCS-943: ...",
        "body": "",
        "head": {
            "ref": "YDBDOCS-943-feature-branch",
            "sha": _git_head(git_repo),
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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
                            return_value=[("ydb/docs/en/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_deletes_stale_fixup_branch_at_start(git_repo: str):
    """Re-run deletes ydbdoc-review/verify-N before LLM work (§6.136)."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "docs bilingual",
        "body": "",
        "head": {
            "ref": "feature/docs",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
    }
    changes = [
        ("ydb/docs/ru/core/a.md", "modified"),
        ("ydb/docs/en/core/a.md", "modified"),
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_translation_pr_pushes_fixes_inline(git_repo: str):
    """Translation PR: critic fixes commit on ydbdoc-review/pr-N, no fixup PR (§6.75)."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": _git_head(git_repo),
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
                            return_value=[("ydb/docs/en/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_same_repo_author_pr_opens_fixup_pr(git_repo: str):
    """Unmerged same-repo PR: never push critic fixes to the author's head branch."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "docs: feature",
        "body": "",
        "head": {
            "ref": "feature/docs",
            "sha": _git_head(git_repo),
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
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
                            return_value=[("ydb/docs/en/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_posts_comment(git_repo: str):
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")
    content_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"], text=True
    ).strip()

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": _git_head(git_repo),
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
                            return_value=[("ydb/docs/en/core/a.md", "modified")],
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_bilingual_source_pr_no_completeness_gaps(git_repo: str):
    """Author PR with RU+EN in the same diff: completeness OK, locales from checkout."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True, exist_ok=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")

    pull = {
        "title": "YDBDOCS-2562: fix resource_weight (mentions PR #999 noise)",
        "body": "Fix typo in RU and EN.",
        "head": {
            "ref": "fix/YDBDOCS-2562-fix",
            "sha": _git_head(git_repo),
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
        "merged": True,
        "merge_commit_sha": "mergeabc",
    }
    changes = [
        ("ydb/docs/ru/core/a.md", "modified"),
        ("ydb/docs/en/core/a.md", "modified"),
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


@pytest.mark.usefixtures("ops_isolation")
def test_run_doc_verify_bilingual_source_pr_ru_only_completeness_gap(git_repo: str):
    """Author PR that changes RU without EN mirror → completeness 🔴."""
    pull = {
        "title": "docs: RU-only tweak",
        "body": "",
        "head": {
            "ref": "docs/ru-only",
            "sha": _git_head(git_repo),
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": _git_head(git_repo)},
    }
    changes = [("ydb/docs/ru/core/a.md", "modified")]
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
    assert result.pr_result.completeness_gaps == ["ydb/docs/en/core/a.md"]
