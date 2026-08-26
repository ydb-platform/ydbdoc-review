"""Tests for doc_translate / doc_verify workflow."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.errors import GitHubAPIError, GitHubConfigError
from ydbdoc_review.github.workflow import (
    DocJobResult,
    _apply_results_to_disk,
    _enforce_report_checkout_bytes,
    _hard_validation_errors,
    run_doc_continue,
    run_doc_translate,
    run_doc_verify,
)
from ydbdoc_review.llm.client import ChatResult, YandexLLMClient
from ydbdoc_review.llm.usage import LLMUsage, UsageTracker
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


def _workflow_llm() -> YandexLLMClient:
    class FakeClient:
        def __init__(self):
            self.usage_tracker = UsageTracker()
            self.transcript_recorder = None

        def model_chain_for_role(self, role):
            del role
            return ["fake-model"]

        def chat(self, messages, **kwargs):
            del kwargs
            prompt = json.dumps(messages, ensure_ascii=False)
            segment_ids = list(dict.fromkeys(re.findall(r"s\d{4}", prompt)))
            response = (
                '{"verdict":"ok","issues":[]}'
                if "verdict" in prompt.casefold()
                else json.dumps(
                    {
                        "segments": [
                            {
                                "id": item,
                                "text": "Current EN meaning from the old path.",
                            }
                            for item in segment_ids
                        ]
                    }
                )
                if segment_ids
                else '{"verdict":"ok","issues":[]}'
            )
            return ChatResult(
                content=response,
                model_slug="fake-model",
                model_uri="fake://model",
                usage=LLMUsage(
                    model_slug="fake-model",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=1.0,
                    retries=0,
                    success=True,
                ),
            )
    return FakeClient()  # type: ignore[return-value]


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


def _make_en_a_reachable(repo_path: str) -> None:
    """Give intended-valid workflow mocks a current EN toc for their target."""
    en_core = Path(repo_path) / "ydb" / "docs" / "en" / "core"
    en_core.mkdir(parents=True, exist_ok=True)
    (en_core / "toc_p.yaml").write_text(
        "items:\n- name: A\n  href: ../a.md\n", encoding="utf-8"
    )


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
        authoritative_source_text="Привет.\n",
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
    file_result = result.pair_results[0].file_result
    assert file_result is not None
    file_result.heuristic_blocking.append(
        "md_link_parity: EN missing RU links: candidate-only.md"
    )
    with patch(
        "ydbdoc_review.github.workflow.read_text_at_ref",
        return_value="Different committed bytes.\n",
    ):
        mismatches = _enforce_report_checkout_bytes("/repo", "abc123", result)

    assert mismatches == ["ydb/docs/en/a.md"]
    assert file_result.verdict == "blocked"
    assert file_result.final_text == "Different committed bytes.\n"
    assert not any(
        "candidate-only.md" in message for message in file_result.heuristic_blocking
    )
    assert any(
        message.startswith("report_checkout_mismatch:")
        for message in file_result.heuristic_blocking
    )


def test_apply_results_transaction_rolls_back_all_paths_on_write_failure(
    tmp_path: Path,
):
    old = "ydb/docs/en/old.md"
    new = "ydb/docs/en/new.md"
    toc = "ydb/docs/en/toc.yaml"
    for rel, body in ((old, "old bytes\n"), (toc, "old toc\n")):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    pair = DocPair(
        ru_path="ydb/docs/ru/new.md",
        en_path=new,
        ru_changed=True,
        previous_en_path=old,
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, new, "ru", "en")
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="new bytes\n",
                additional_delete_paths=(old,),
            )
        ],
        navigation_results=[
            NavigationRunResult("ydb/docs/ru/toc.yaml", toc, "toc", "new toc\n")
        ],
    )
    real_replace = os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected transaction failure")
        real_replace(source, destination)

    with patch("ydbdoc_review.github.workflow.os.replace", side_effect=fail_second_replace):
        with pytest.raises(OSError, match="injected transaction failure"):
            _apply_results_to_disk(str(tmp_path), result, dry_run=False)

    assert (tmp_path / old).read_text(encoding="utf-8") == "old bytes\n"
    assert not (tmp_path / new).exists()
    assert (tmp_path / toc).read_text(encoding="utf-8") == "old toc\n"


def test_apply_results_transaction_cleans_stage_and_created_dirs_on_stream_failure(
    tmp_path: Path,
):
    result = _fake_pr_result()
    result.pair_results[0].plan = replace(
        result.pair_results[0].plan,
        target_path="new/nested/ydb/docs/en/a.md",
    )
    real_fdopen = os.fdopen

    class FailingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, text):
            self.stream.write(text[:1])
            raise OSError("injected staging write failure")

        def __exit__(self, exc_type, exc, traceback):
            return self.stream.__exit__(exc_type, exc, traceback)

    def failing_fdopen(fd, *args, **kwargs):
        return FailingStream(real_fdopen(fd, *args, **kwargs))

    with patch("ydbdoc_review.github.workflow.os.fdopen", side_effect=failing_fdopen):
        with pytest.raises(OSError, match="injected staging write failure"):
            _apply_results_to_disk(str(tmp_path), result, dry_run=False)

    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.rglob(".a.md.*"))


def test_apply_results_transaction_cleans_stage_on_stream_close_failure(tmp_path: Path):
    result = _fake_pr_result()
    real_fdopen = os.fdopen

    class CloseFailingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self.stream

        def __exit__(self, exc_type, exc, traceback):
            self.stream.__exit__(exc_type, exc, traceback)
            raise OSError("injected staging close failure")

    def failing_fdopen(fd, *args, **kwargs):
        return CloseFailingStream(real_fdopen(fd, *args, **kwargs))

    with patch("ydbdoc_review.github.workflow.os.fdopen", side_effect=failing_fdopen):
        with pytest.raises(OSError, match="injected staging close failure"):
            _apply_results_to_disk(str(tmp_path), result, dry_run=False)

    assert not (tmp_path / "ydb/docs/en/a.md").exists()
    assert not list(tmp_path.rglob(".a.md.*"))


def test_apply_results_transaction_rolls_back_previous_unlink_failure(tmp_path: Path):
    old = "ydb/docs/en/old.md"
    new = "ydb/docs/en/new.md"
    old_path = tmp_path / old
    old_path.parent.mkdir(parents=True)
    old_path.write_text("old bytes\n", encoding="utf-8")
    pair = DocPair(
        ru_path="ydb/docs/ru/new.md",
        en_path=new,
        ru_changed=True,
        previous_en_path=old,
    )
    plan = PairPlan(pair, "translate_to_en", pair.ru_path, new, "ru", "en")
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="new bytes\n",
                additional_delete_paths=(old,),
            )
        ]
    )
    real_unlink = Path.unlink
    failed = False

    def fail_previous_once(path, *args, **kwargs):
        nonlocal failed
        if path == old_path and not failed:
            failed = True
            raise OSError("injected previous unlink failure")
        return real_unlink(path, *args, **kwargs)

    with patch("ydbdoc_review.github.workflow.Path.unlink", new=fail_previous_once):
        with pytest.raises(OSError, match="injected previous unlink failure"):
            _apply_results_to_disk(str(tmp_path), result, dry_run=False)

    assert old_path.read_text(encoding="utf-8") == "old bytes\n"
    assert not (tmp_path / new).exists()
    assert not list(tmp_path.rglob(".new.md.*"))


def test_report_checkout_guard_keeps_real_checkout_link_issue():
    result = _fake_pr_result()
    pair = result.pair_results[0]
    pair.source_text = "See [required](required.md).\n"
    file_result = pair.file_result
    assert file_result is not None
    file_result.heuristic_blocking.append("candidate-only issue")
    with patch(
        "ydbdoc_review.github.workflow.read_text_at_ref",
        return_value="Committed text without the required link.\n",
    ):
        mismatches = _enforce_report_checkout_bytes("/repo", "abc123", result)

    assert mismatches == [pair.plan.target_path]
    assert not any("candidate-only" in item for item in file_result.heuristic_blocking)
    assert any(
        "required.md" in item and "missing" in item
        for item in file_result.heuristic_blocking
    )


def test_hard_validation_ignores_unrelated_current_drift_for_historical_result():
    result = _fake_pr_result()
    run = result.pair_results[0]
    run.historical_disposition = "already_translated"
    run.plan = replace(
        run.plan,
        authoritative_source_text="```python\nprint('new RU')\n```\n",
    )
    run.target_text = "Current EN without that later, unrelated fence.\n"

    assert _hard_validation_errors(result) == []


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


def test_run_doc_translate_merged_pr_preserves_existing_en(git_repo: str):
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
        return_value=_fake_pr_result(),
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
    verify_pairs.assert_not_called()
    translate_pairs.assert_called_once()
    assert translate_pairs.call_args.kwargs["historical_merged_provenance"] is True


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
                        return_value=True,
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


def test_merged_source_snapshot_unavailable_fails_closed(git_repo: str):
    pull = {
        "title": "Historical docs",
        "merged": True,
        "merge_commit_sha": "deadbeef",
        "head": {
            "ref": "historical-docs",
            "sha": "cafebabe",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        mock_gh.return_value.get_pull.return_value = pull
        with patch("ydbdoc_review.github.workflow.ensure_commit", return_value=False):
            with pytest.raises(RuntimeError, match="official merged source snapshot"):
                run_doc_translate(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=45949,
                    dry_run=True,
                    config=load_config(env=_env()),
                )


def test_run_doc_translate_45949_move_and_50857_tombstone_transaction(git_repo: str):
    repo = Path(git_repo)
    old_ru = "ydb/docs/ru/core/devops/deployment-options/manual/node-authorization.md"
    new_ru = "ydb/docs/ru/core/devops/concepts/node-authorization.md"
    old_en = old_ru.replace("/ru/", "/en/")
    new_en = new_ru.replace("/ru/", "/en/")
    dynamic_ru = "ydb/docs/ru/core/maintenance/manual/dynamic-config.md"
    dynamic_en = dynamic_ru.replace("/ru/", "/en/")
    client_ru = "ydb/docs/ru/core/reference/configuration/client_certificate_authorization.md"
    client_en = client_ru.replace("/ru/", "/en/")
    index_ru = "ydb/docs/ru/core/devops/concepts/index.md"
    index_en = index_ru.replace("/ru/", "/en/")
    concepts_toc_ru = "ydb/docs/ru/core/devops/concepts/toc_p.yaml"
    concepts_toc_en = concepts_toc_ru.replace("/ru/", "/en/")
    manual_toc_ru = "ydb/docs/ru/core/devops/deployment-options/manual/toc_p.yaml"
    manual_toc_en = manual_toc_ru.replace("/ru/", "/en/")
    redirects = "ydb/docs/redirects.yaml"
    en_toc = "ydb/docs/en/core/toc_p.yaml"

    base_files = {
        old_ru: "# Authentication and authorization of database nodes\n\nNode certificate.\n",
        old_en: "# Node authentication\n\nCurrent EN meaning from the old path.\n",
        dynamic_ru: "# Dynamic config\n\nRU page.\n",
        dynamic_en: "# Dynamic config\n\nHistorical EN page.\n",
        client_ru: "# Client certificate\n\nSee node authorization.\n",
        client_en: "# Client certificate\n\nSee [node authorization](../../devops/concepts/node-authorization.md).\n",
        index_ru: "# Concepts\n\n- [Node authorization](../deployment-options/manual/node-authorization.md)\n",
        index_en: "# Concepts\n\n- [Node authorization](../deployment-options/manual/node-authorization.md)\n",
        concepts_toc_ru: "items:\n- name: Concepts\n  href: index.md\n",
        concepts_toc_en: "items:\n- name: Concepts\n  href: index.md\n",
        manual_toc_ru: "items:\n- name: Node authorization\n  href: node-authorization.md\n",
        manual_toc_en: "items:\n- name: Node authorization\n  href: node-authorization.md\n",
        en_toc: "items:\n- include:\n    path: devops/concepts/toc_p.yaml\n"
        "- include:\n    path: devops/deployment-options/manual/toc_p.yaml\n"
        "- href: maintenance/manual/dynamic-config.md\n",
    }
    for path, body in base_files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "45949 base"], cwd=repo, check=True)
    (repo / old_ru).unlink()
    new_target = repo / new_ru
    new_target.parent.mkdir(parents=True, exist_ok=True)
    new_target.write_text(
        "# Configuring authentication and authorization of database nodes\n\n"
        "Node certificate and registration.\n",
        encoding="utf-8",
    )
    (repo / dynamic_ru).write_text("# Dynamic config\n\nHistorical source update.\n", encoding="utf-8")
    (repo / client_ru).write_text("# Client certificate\n\nSee the moved node authorization.\n", encoding="utf-8")
    (repo / index_ru).write_text(
        "# Concepts\n\n- [Node authorization](node-authorization.md)\n", encoding="utf-8"
    )
    (repo / concepts_toc_ru).write_text(
        "items:\n- name: Concepts\n  href: index.md\n"
        "- name: Node authorization\n  href: node-authorization.md\n",
        encoding="utf-8",
    )
    (repo / manual_toc_ru).write_text("items: []\n", encoding="utf-8")
    (repo / redirects).write_text(
        "ru:\n- from: core/devops/deployment-options/manual/node-authorization.md\n"
        "  to: core/devops/concepts/node-authorization.md\nen: []\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "merge 45949"], cwd=repo, check=True)
    merge_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    (repo / dynamic_en).unlink()
    (repo / client_ru).write_text(
        "# Client certificate\n\n| Later RU-only table formatting |\n| --- |\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "50857 and 50596 current state"], cwd=repo, check=True)

    changes = [
        (old_ru, "deleted"),
        (new_ru, "added"),
        (dynamic_ru, "modified"),
        (client_ru, "modified"),
        (index_ru, "modified"),
        (concepts_toc_ru, "modified"),
        (manual_toc_ru, "modified"),
        (redirects, "modified"),
    ]

    pull = {
        "title": "Move node authorization",
        "merged": True,
        "merge_commit_sha": merge_sha,
        "head": {"ref": "source", "sha": merge_sha, "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"}},
        "base": {"ref": "main"},
    }
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh, patch(
        "ydbdoc_review.github.workflow.ensure_commit", return_value=True
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git", return_value=changes
    ), patch(
        "ydbdoc_review.github.workflow.create_llm_client",
        return_value=_workflow_llm(),
    ), patch(
        "ydbdoc_review.github.workflow.prepare_translation_branch_on_base"
    ), patch(
        "ydbdoc_review.github.workflow.git_commit_paths", return_value=True
    ), patch("ydbdoc_review.github.workflow.push_branch"), patch(
        "ydbdoc_review.github.workflow.run_doc_verify", return_value=_mock_inline_verify_job()
    ):
        client = mock_gh.return_value
        client.get_pull.return_value = pull
        client.iter_pull_files.return_value = [
            {"filename": old_ru, "status": "removed"},
            {"filename": new_ru, "status": "added"},
            {"filename": dynamic_ru, "status": "modified"},
            {"filename": client_ru, "status": "modified"},
            {"filename": index_ru, "status": "modified"},
            {"filename": concepts_toc_ru, "status": "modified"},
            {"filename": manual_toc_ru, "status": "modified"},
            {"filename": redirects, "status": "modified"},
        ]
        client.create_pull.return_value = ("https://github.com/o/r/pull/99", 99, True)
        client.iter_issue_comments.return_value = iter([])
        client.post_issue_comment.return_value = "url"
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=45949,
            merge_base_with="HEAD",
            dry_run=False,
            config=load_config(env=_env()),
        )

    assert result.pr_result.completeness_gaps == []
    assert result.committed and result.pushed
    assert (repo / new_en).read_text(encoding="utf-8").endswith("Current EN meaning from the old path.\n")
    assert not (repo / old_en).exists()
    assert not (repo / dynamic_en).exists()
    assert result.pr_result.completeness_states[dynamic_en] == "superseded_absent"
    assert "node-authorization.md" in (repo / index_en).read_text(encoding="utf-8")
    assert "node-authorization.md" in (repo / concepts_toc_en).read_text(
        encoding="utf-8"
    )
    assert "node-authorization.md" not in (repo / manual_toc_en).read_text(
        encoding="utf-8"
    )
    assert (repo / client_en).read_text(encoding="utf-8") == base_files[client_en]


def test_run_doc_translate_posts_comments(git_repo: str):
    _make_en_a_reachable(git_repo)
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
    _make_en_a_reachable(git_repo)
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
    _make_en_a_reachable(git_repo)
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
    # The mocked verifier returns ``Hello.\n``.  Keep the checkout distinct so
    # this test exercises a real fixup rather than the no-op writer contract.
    (en / "a.md").write_text("Stale translation.\n", encoding="utf-8")
    _make_en_a_reachable(git_repo)

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
                            return_value=[
                                ("ydb/docs/ru/a.md", "modified"),
                                ("ydb/docs/en/a.md", "modified"),
                            ],
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
    (en / "a.md").write_text("Stale translation.\n", encoding="utf-8")
    _make_en_a_reachable(git_repo)

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
                            return_value=[
                                ("ydb/docs/ru/a.md", "modified"),
                                ("ydb/docs/en/a.md", "modified"),
                            ],
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
    _make_en_a_reachable(git_repo)

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
    (en / "a.md").write_text("Stale translation.\n", encoding="utf-8")
    _make_en_a_reachable(git_repo)

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
                            return_value=[
                                ("ydb/docs/ru/a.md", "modified"),
                                ("ydb/docs/en/a.md", "modified"),
                            ],
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
    _make_en_a_reachable(git_repo)
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
    assert result.pr_result.completeness_gaps == [
        "qa_blocked:ydb/docs/en/a.md",
        "ydb/docs/en/a.md",
    ]
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
