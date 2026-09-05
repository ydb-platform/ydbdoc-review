"""Top-level publication contract for paid doc_translate candidates."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import Timeout

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.errors import GitHubAPIError
from ydbdoc_review.github.workflow import (
    job_requires_nonzero_exit,
    run_doc_translate,
    run_doc_verify,
)
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.lifecycle import append_retention_footer
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_commit_message,
    build_full_report,
    build_source_pr_comment,
    build_translation_pr_body,
    parse_final_tree_blocker_manifest,
    result_has_blocking_findings,
)
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.validation.href_parity import check_href_parity
from ydbdoc_review.validation.link_contract import LinkContractIssue


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
def publication_repo(tmp_path: Path) -> str:
    """Real git tree used by production scope and final-tree readers."""
    repo = tmp_path / "repo"
    ru = repo / "ydb" / "docs" / "ru"
    en = repo / "ydb" / "docs" / "en"
    en_core = en / "core"
    ru.mkdir(parents=True)
    en_core.mkdir(parents=True)
    (ru / "a.md").write_text("Привет.\n", encoding="utf-8")
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")
    (en_core / "toc_p.yaml").write_text(
        "items:\n- name: A\n  href: ../a.md\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    return str(repo)


def _pair_result(
    *,
    target_text: str | None = "Translated.\n",
    error: str | None = None,
    warnings: list[str] | None = None,
    blocking: list[str] | None = None,
    manual_actions: list[ManualAction] | None = None,
    alignment_error: str | None = None,
    link_issues: tuple[LinkContractIssue, ...] = (),
) -> PRTranslationResult:
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
    file_result = None
    if target_text is not None:
        file_result = FileTranslationResult(
            file_path=pair.en_path,
            final_text=target_text,
            segments_count=1,
            verdict="blocked" if alignment_error or link_issues else "ok",
            prompt_version="v1",
            heuristic_warnings=list(warnings or ()),
            heuristic_blocking=list(blocking or ()),
            manual_actions=list(manual_actions or ()),
            segment_alignment_error=alignment_error,
            link_contract_issues=link_issues,
        )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text=target_text,
                file_result=file_result,
                error=error,
                source_text="Привет.\n",
                validation_issues=link_issues,
            )
        ]
    )


def _soft_keep_blocker(path: str, text: str) -> FinalTreeBlocker:
    return FinalTreeBlocker(
        path=path,
        code="translation_soft_keep",
        message=(
            "translation_soft_keep: Invalid JSON in LLM response. Действие: "
            "вручную обновить EN в этой ветке, затем запустить doc_verify"
        ),
        artifact_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def _verify_pair_result(path: str, target_text: str, *, unsafe: bool = False):
    ru_path = path.replace("/en/", "/ru/")
    pair = DocPair(ru_path=ru_path, en_path=path, ru_changed=True, en_changed=True)
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=PairPlan(
                    pair=pair,
                    action="critic_only",
                    source_path=ru_path,
                    target_path=path,
                    source_lang="ru",
                    target_lang="en",
                ),
                target_text=target_text,
                source_text="# Аутентификация\n\nОбновлённый русский текст.\n",
                file_result=FileTranslationResult(
                    file_path=path,
                    final_text=target_text,
                    segments_count=1,
                    verdict="blocked" if unsafe else "ok",
                    prompt_version="verify",
                    heuristic_blocking=(
                        ["heading_parity: required heading is missing"] if unsafe else []
                    ),
                ),
            )
        ]
    )


def _run_top_level(
    repo_path: str,
    pr_result: PRTranslationResult,
    *,
    existing_pr: bool = False,
    late_existing_pr: bool = False,
    remote_branch_exists: bool | None = None,
    impact_path: str | None = None,
    create_succeeds: bool = True,
    source_changes: list[tuple[str, str]] | None = None,
    event_log: list[str] | None = None,
    draft_conversion_fails: bool = False,
    draft_conversion_fail_on_call: int | None = None,
    postpush_pr_draft: bool | None = None,
    postpush_confirmation_error: BaseException | None = None,
    rollback_error: Exception | None = None,
    prepush_create_returns_none: bool = False,
    real_push_remote: str | None = None,
    remote_branch_sha: str | None = None,
    push_fails: bool = False,
    commit_succeeds: bool = True,
    real_git_commit: bool = False,
    reconcile_side_effect=None,
    existing_pr_body: str = "",
    existing_pr_draft: bool = True,
):
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    gh = MagicMock()

    def _get_pull(_owner, _repo, number):
        if number == 7:
            return pull
        if event_log is not None:
            event_log.append("refetch")
        if postpush_confirmation_error is not None:
            raise postpush_confirmation_error
        draft = postpush_pr_draft
        if draft is None:
            draft = existing_pr_draft
        return {
            "draft": draft,
            "body": existing_pr_body,
            "head": {
                "ref": "ydbdoc-review/pr-7",
                "sha": remote_branch_sha or "old-remote-sha",
                "repo": {
                    "clone_url": "https://github.com/o/r.git",
                    "full_name": "o/r",
                },
            },
            "base": {"ref": "main"},
        }

    gh.get_pull.side_effect = _get_pull
    gh.iter_issue_comments.return_value = iter(())
    branch_was_present = (
        late_existing_pr if remote_branch_exists is None else remote_branch_exists
    )
    gh.get_branch_sha.return_value = (
        remote_branch_sha or "old-remote-sha" if branch_was_present else None
    )
    created_pull = (
        (
            "https://github.com/o/r/pull/99",
            99,
            not existing_pr and not late_existing_pr,
        )
        if create_succeeds
        else None
    )
    gh.find_open_pull_by_head.side_effect = lambda *_args, **_kwargs: (
        event_log.append("discover") if event_log is not None else None
    ) or (("https://github.com/o/r/pull/99", 99) if existing_pr else None)
    create_calls = 0

    def _create(*_args, **_kwargs):
        nonlocal create_calls
        create_calls += 1
        if event_log is not None:
            event_log.append("create")
        if prepush_create_returns_none and create_calls == 1:
            return None
        return created_pull

    gh.create_pull.side_effect = _create
    gh.update_pull_body.side_effect = lambda *_args, **_kwargs: (
        event_log.append("body") if event_log is not None else None
    )

    convert_calls = 0

    def _convert(*_args, **_kwargs):
        nonlocal convert_calls
        convert_calls += 1
        if event_log is not None:
            event_log.append("draft")
        if draft_conversion_fails or draft_conversion_fail_on_call == convert_calls:
            raise GitHubAPIError("cannot convert", status_code=403)
        return True

    gh.convert_pull_to_draft.side_effect = _convert
    gh.post_issue_comment.return_value = "https://github.com/o/r/pull/7#issuecomment-1"
    ops_ctx = SimpleNamespace(recorder=None, continue_feedback=None)

    with ExitStack() as stack:
        stack.enter_context(patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh))
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.begin_ops_job",
                return_value=(ops_ctx, GateResult(ok=True), None),
            )
        )
        finish = stack.enter_context(patch("ydbdoc_review.github.workflow.finish_ops_job"))
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=(
                    [("ydb/docs/ru/a.md", "modified")]
                    if source_changes is None
                    else source_changes
                ),
            )
        )
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[])
        )
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=pr_result)
        )
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.apply_orphan_toc_page_checks", return_value=[])
        )
        if impact_path is not None:
            stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow._declare_exact_ascii_fragment_targets_after_apply",
                    return_value=[impact_path],
                )
            )
        if reconcile_side_effect is not None:
            stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow._reconcile_final_en_same_fragment_paths_after_apply",
                    side_effect=reconcile_side_effect,
                )
            )
        if real_git_commit:
            import ydbdoc_review.github.workflow as workflow

            stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow.translation_branch_base",
                    return_value=(repo_path, "main"),
                )
            )
            prepare = stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow.prepare_translation_branch_on_base",
                    wraps=workflow.prepare_translation_branch_on_base,
                )
            )
            commit = stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow.git_commit_paths",
                    wraps=workflow.git_commit_paths,
                )
            )
        else:
            prepare = stack.enter_context(
                patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base")
            )
            commit = stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow.git_commit_paths",
                    return_value=commit_succeeds,
                )
            )
        if real_push_remote is None:
            push = stack.enter_context(patch("ydbdoc_review.github.workflow.push_branch"))
            rollback = stack.enter_context(
                patch("ydbdoc_review.github.workflow.rollback_pushed_branch")
            )
            if event_log is not None:
                def _rollback_mock(*_args, **_kwargs):
                    event_log.append("rollback")
                    if rollback_error is not None:
                        raise rollback_error

                rollback.side_effect = _rollback_mock
            if event_log is not None:
                def _push_mock(*_args, **_kwargs):
                    event_log.append("push")
                    if push_fails:
                        raise RuntimeError("guarded push lease failed")

                push.side_effect = _push_mock
            elif push_fails:
                push.side_effect = RuntimeError("guarded push lease failed")
        else:
            import ydbdoc_review.github.workflow as workflow

            real_push = workflow.push_branch

            def _push_real(*args, **kwargs):
                if event_log is not None:
                    event_log.append("push")
                return real_push(*args, **kwargs)

            stack.enter_context(
                patch(
                    "ydbdoc_review.github.git_ops.remote_push_url",
                    return_value=real_push_remote,
                )
            )
            push = stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow.push_branch",
                    side_effect=_push_real,
                )
            )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.run_doc_verify",
                return_value=SimpleNamespace(
                    translation_comment_url="https://github.com/o/r/pull/99#issuecomment-2",
                    pr_result=pr_result,
                ),
            )
        )
        job = run_doc_translate(
            repo_path=repo_path,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
        )
    return job, gh, prepare, commit, push, finish


def test_safe_final_link_blocker_publishes_draft_red(publication_repo: str):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    result.pair_results[0].source_text = "См. [missing](missing.md).\n"

    job, gh, prepare, commit, push, finish = _run_top_level(publication_repo, result)

    assert job.pr_result.completeness_gaps == []
    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_RED"
    blockers = getattr(job.pr_result, "final_tree_blockers", ())
    assert [(b.path, b.code) for b in blockers] == [
        ("ydb/docs/en/a.md", "en_link_target")
    ]
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert push.call_args.kwargs["guard_remote_ref"] is True
    assert push.call_args.kwargs["expected_remote_sha"] is None
    gh.create_pull.assert_called_once()
    assert gh.create_pull.call_args.kwargs["draft"] is True
    pr_body = gh.create_pull.call_args.kwargs["body"]
    assert "QA RED, do not merge" in pr_body
    assert "missing.md" in pr_body
    source_summary = gh.post_issue_comment.call_args.args[3]
    assert "published_red" in source_summary
    assert "🔴" in source_summary and "не мержить" in source_summary
    full_report = build_full_report(
        job.pr_result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=load_config(env=_env()),
    )
    assert "QA RED, do not merge" in full_report
    assert "missing.md" in full_report
    assert finish.call_args.kwargs["status"] == "published_red"
    assert job_requires_nonzero_exit(job) is False


def test_safe_soft_keep_with_eight_git_artifacts_publishes_draft_red(
    publication_repo: str,
):
    repo = Path(publication_repo)
    pair_results: list[PairRunResult] = []
    source_changes: list[tuple[str, str]] = []
    for index in range(1, 9):
        ru_path = f"ydb/docs/ru/page-{index}.md"
        en_path = f"ydb/docs/en/page-{index}.md"
        Path(repo, ru_path).write_text(f"# Страница {index}\n", encoding="utf-8")
        Path(repo, en_path).write_text(f"# Old page {index}\n", encoding="utf-8")
        pair = DocPair(ru_path=ru_path, en_path=en_path, ru_changed=True)
        pair_results.append(
            PairRunResult(
                plan=PairPlan(
                    pair=pair,
                    action="translate_to_en",
                    source_path=ru_path,
                    target_path=en_path,
                    source_lang="ru",
                    target_lang="en",
                ),
                target_text=f"# Translated page {index}\n",
                source_text=f"# Страница {index}\n",
                file_result=FileTranslationResult(
                    file_path=en_path,
                    final_text=f"# Translated page {index}\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="v1",
                ),
            )
        )
        source_changes.append((ru_path, "modified"))

    auth_ru = "ydb/docs/ru/core/security/authentication.md"
    auth_en = "ydb/docs/en/core/security/authentication.md"
    retained = "# Authentication\n\nExisting reviewed English.\n"
    Path(repo, auth_ru).parent.mkdir(parents=True, exist_ok=True)
    Path(repo, auth_en).parent.mkdir(parents=True, exist_ok=True)
    Path(repo, auth_ru).write_text(
        "# Аутентификация\n\nОбновлённый русский текст.\n",
        encoding="utf-8",
    )
    Path(repo, auth_en).write_text(retained, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "nine-pair baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    auth_pair = DocPair(ru_path=auth_ru, en_path=auth_en, ru_changed=True)
    soft_run = PairRunResult(
        plan=PairPlan(
            pair=auth_pair,
            action="translate_to_en",
            source_path=auth_ru,
            target_path=auth_en,
            source_lang="ru",
            target_lang="en",
        ),
        target_text=retained,
        source_text=Path(repo, auth_ru).read_text(encoding="utf-8"),
        file_result=FileTranslationResult(
            file_path=auth_en,
            final_text=retained,
            segments_count=0,
            verdict="warnings",
            prompt_version="soft-keep",
            heuristic_warnings=[
                "translate_soft_keep: translate failed; kept tip EN unchanged"
            ],
        ),
    )
    soft_run.soft_keep_reason = "Invalid JSON in LLM response"
    pair_results.append(soft_run)
    source_changes.append((auth_ru, "modified"))
    result = PRTranslationResult(pair_results=pair_results)

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
        source_changes=source_changes,
    )

    assert job.pr_result.publication_impact == "PUBLISH_RED"
    assert job.pr_result.translated_count == 8
    assert job.pr_result.retained_count == 1
    assert job.pr_result.failed_count == 0
    assert job.pr_result.completeness_gaps == []
    assert prepare.call_count == commit.call_count == push.call_count == 1
    assert push.call_args.kwargs["guard_remote_ref"] is True
    assert gh.create_pull.call_args.kwargs["draft"] is True
    body = gh.create_pull.call_args.kwargs["body"]
    assert auth_en in body
    assert "Invalid JSON in LLM response" in body
    assert "вручную обновить EN" in body
    blocker = next(b for b in job.pr_result.final_tree_blockers if b.path == auth_en)
    assert blocker.code == "translation_soft_keep"
    assert blocker.artifact_sha256 == hashlib.sha256(retained.encode()).hexdigest()
    assert finish.call_args.kwargs["status"] == "published_red"
    assert job_requires_nonzero_exit(job) is False


def test_real_git_soft_keep_commit_contains_eight_translations_and_retained_auth(
    publication_repo: str,
):
    repo = Path(publication_repo)
    pair_results: list[PairRunResult] = []
    source_changes: list[tuple[str, str]] = []
    expected_translations: dict[str, str] = {}
    for index in range(1, 9):
        ru_path = f"ydb/docs/ru/page-{index}.md"
        en_path = f"ydb/docs/en/page-{index}.md"
        source = f"# Страница {index}\n"
        baseline = f"# Old page {index}\n"
        translated = f"# Exact translated page {index}\n"
        Path(repo, ru_path).write_text(source, encoding="utf-8")
        Path(repo, en_path).write_text(baseline, encoding="utf-8")
        pair = DocPair(ru_path=ru_path, en_path=en_path, ru_changed=True)
        pair_results.append(
            PairRunResult(
                plan=PairPlan(
                    pair=pair,
                    action="translate_to_en",
                    source_path=ru_path,
                    target_path=en_path,
                    source_lang="ru",
                    target_lang="en",
                ),
                target_text=translated,
                source_text=source,
                file_result=FileTranslationResult(
                    file_path=en_path,
                    final_text=translated,
                    segments_count=1,
                    verdict="ok",
                    prompt_version="v1",
                ),
            )
        )
        source_changes.append((ru_path, "modified"))
        expected_translations[en_path] = translated

    auth_ru = "ydb/docs/ru/core/security/authentication.md"
    auth_en = "ydb/docs/en/core/security/authentication.md"
    retained = "# Authentication\n\nExisting reviewed English.\n"
    Path(repo, auth_ru).parent.mkdir(parents=True, exist_ok=True)
    Path(repo, auth_en).parent.mkdir(parents=True, exist_ok=True)
    Path(repo, auth_ru).write_text("# Аутентификация\n", encoding="utf-8")
    Path(repo, auth_en).write_text(retained, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "nine-pair real-git baseline"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    auth_pair = DocPair(ru_path=auth_ru, en_path=auth_en, ru_changed=True)
    pair_results.append(
        PairRunResult(
            plan=PairPlan(
                pair=auth_pair,
                action="translate_to_en",
                source_path=auth_ru,
                target_path=auth_en,
                source_lang="ru",
                target_lang="en",
            ),
            target_text=retained,
            source_text="# Аутентификация\n",
            soft_keep_reason="Invalid JSON in LLM response",
            file_result=FileTranslationResult(
                file_path=auth_en,
                final_text=retained,
                segments_count=0,
                verdict="warnings",
                prompt_version="soft-keep",
            ),
        )
    )
    source_changes.append((auth_ru, "modified"))

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        PRTranslationResult(pair_results=pair_results),
        source_changes=source_changes,
        real_git_commit=True,
    )

    assert job.committed is True
    assert prepare.call_count == commit.call_count == push.call_count == 1
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "ydbdoc-review/pr-7"
    assert subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == base_sha
    for path, expected in expected_translations.items():
        assert subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout == expected
    assert subprocess.run(
        ["git", "show", f"HEAD:{auth_en}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == retained
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD^", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert changed == sorted(expected_translations)
    assert gh.create_pull.call_args.kwargs["draft"] is True


def test_real_git_soft_keep_without_diff_creates_no_artifact_pr(
    publication_repo: str,
):
    result = _pair_result(target_text="Hello.\n")
    result.pair_results[0].soft_keep_reason = "translation timed out"

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
        real_git_commit=True,
    )

    assert prepare.call_count == commit.call_count == 1
    assert job.committed is False
    assert job.pr_result.publication_failure == "no_publishable_artifact"
    assert job.translation_pr_number is None
    assert job_requires_nonzero_exit(job) is True
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "failed"


def test_soft_keep_no_new_commit_reuses_matching_existing_draft_artifact_pr(
    publication_repo: str,
):
    retained = "Hello.\n"
    result = _pair_result(target_text=retained)
    result.pair_results[0].soft_keep_reason = "translation timed out"
    published = PRTranslationResult(
        final_tree_blockers=[_soft_keep_blocker("ydb/docs/en/a.md", retained)],
        publication_impact=PublicationImpact.PUBLISH_RED,
    )
    existing_body = build_translation_pr_body(
        7,
        "o/r",
        publication_result=published,
    )

    job, gh, _prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
        existing_pr=True,
        remote_branch_exists=True,
        remote_branch_sha="artifact-sha",
        existing_pr_body=existing_body,
        existing_pr_draft=True,
        commit_succeeds=False,
    )

    assert commit.call_count == 1
    assert job.committed is False
    assert job.pushed is False
    assert job.pr_result.publication_failure is None
    assert job.pr_result.publication_impact == PublicationImpact.PUBLISH_RED
    assert job.translation_pr_number == 99
    assert job.translation_pr_url == "https://github.com/o/r/pull/99"
    assert job_requires_nonzero_exit(job) is False
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    gh.update_pull_body.assert_called_once()
    assert finish.call_args.kwargs["status"] == "published_red"


def test_soft_keep_no_new_commit_rejects_nonmatching_existing_artifact(
    publication_repo: str,
):
    retained = "Hello.\n"
    result = _pair_result(target_text=retained)
    result.pair_results[0].soft_keep_reason = "translation timed out"
    stale = PRTranslationResult(
        final_tree_blockers=[
            _soft_keep_blocker("ydb/docs/en/a.md", "Different published bytes.\n")
        ],
        publication_impact=PublicationImpact.PUBLISH_RED,
    )

    job, gh, _prepare, _commit, push, finish = _run_top_level(
        publication_repo,
        result,
        existing_pr=True,
        remote_branch_exists=True,
        remote_branch_sha="artifact-sha",
        existing_pr_body=build_translation_pr_body(
            7,
            "o/r",
            publication_result=stale,
        ),
        commit_succeeds=False,
    )

    assert job.pr_result.publication_failure == "no_publishable_artifact"
    assert job.translation_pr_number is None
    assert job_requires_nonzero_exit(job) is True
    push.assert_not_called()
    gh.update_pull_body.assert_not_called()
    assert finish.call_args.kwargs["status"] == "failed"


def test_soft_keep_no_new_commit_existing_ready_artifact_is_drafted_before_body(
    publication_repo: str,
):
    retained = "Hello.\n"
    result = _pair_result(target_text=retained)
    result.pair_results[0].soft_keep_reason = "translation timed out"
    published = PRTranslationResult(
        final_tree_blockers=[_soft_keep_blocker("ydb/docs/en/a.md", retained)],
        publication_impact=PublicationImpact.PUBLISH_RED,
    )
    events: list[str] = []

    job, gh, _prepare, _commit, push, _finish = _run_top_level(
        publication_repo,
        result,
        existing_pr=True,
        remote_branch_exists=True,
        remote_branch_sha="artifact-sha",
        existing_pr_body=build_translation_pr_body(
            7,
            "o/r",
            publication_result=published,
        ),
        existing_pr_draft=False,
        event_log=events,
        commit_succeeds=False,
    )

    assert job.translation_pr_number == 99
    push.assert_not_called()
    gh.convert_pull_to_draft.assert_called_once_with("o", "r", 99)
    assert events[-2:] == ["draft", "body"]


def test_soft_keep_without_target_withholds_incomplete(publication_repo: str):
    result = _pair_result(target_text=None)
    result.pair_results[0].soft_keep_reason = "translation timed out"

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == "WITHHOLD_INCOMPLETE"
    assert job.pr_result.final_tree_blockers == []
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "failed"


def test_soft_keep_with_substituted_nonempty_target_withholds_incomplete(
    publication_repo: str,
):
    result = _pair_result(target_text="Substituted non-empty output.\n")
    result.pair_results[0].soft_keep_reason = "translation timed out"

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
    assert "materialized existing EN" in (job.pr_result.pair_results[0].error or "")
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


def test_soft_keep_does_not_override_completeness_gap(publication_repo: str):
    result = _pair_result(target_text="Hello.\n")
    result.pair_results[0].soft_keep_reason = "translation timed out"

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
        source_changes=[
            ("ydb/docs/ru/a.md", "modified"),
            ("ydb/docs/ru/missing-page.md", "added"),
        ],
    )

    assert "ydb/docs/en/missing-page.md" in job.pr_result.completeness_gaps
    assert job.pr_result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


def test_soft_keep_does_not_override_raw_pair_error(publication_repo: str):
    result = _pair_result(target_text="Hello.\n")
    result.pair_results[0].soft_keep_reason = "translation timed out"
    failed_pair = DocPair(
        ru_path="ydb/docs/ru/b.md",
        en_path="ydb/docs/en/b.md",
        ru_changed=True,
    )
    result.pair_results.append(
        PairRunResult(
            plan=PairPlan(
                pair=failed_pair,
                action="translate_to_en",
                source_path=failed_pair.ru_path,
                target_path=failed_pair.en_path,
                source_lang="ru",
                target_lang="en",
            ),
            target_text="partial output must not make raw error repairable\n",
            error="raw translation failure",
        )
    )

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


def test_soft_keep_for_new_missing_target_withholds_incomplete(publication_repo: str):
    ru_path = "ydb/docs/ru/new-page.md"
    en_path = "ydb/docs/en/new-page.md"
    Path(publication_repo, ru_path).write_text("# Новая страница\n", encoding="utf-8")
    Path(publication_repo, en_path).write_text(
        "# Fabricated retained target\n",
        encoding="utf-8",
    )
    pair = DocPair(ru_path=ru_path, en_path=en_path, ru_changed=True)
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=PairPlan(
                    pair=pair,
                    action="translate_to_en",
                    source_path=ru_path,
                    target_path=en_path,
                    source_lang="ru",
                    target_lang="en",
                ),
                target_text="# Fabricated retained target\n",
                source_text="# Новая страница\n",
                soft_keep_reason="translation timed out",
                file_result=FileTranslationResult(
                    file_path=en_path,
                    final_text="# Fabricated retained target\n",
                    segments_count=0,
                    verdict="warnings",
                    prompt_version="soft-keep",
                ),
            )
        ]
    )

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
        source_changes=[(ru_path, "added")],
    )

    assert job.pr_result.publication_impact == "WITHHOLD_INCOMPLETE"
    assert "materialized existing EN" in (job.pr_result.pair_results[0].error or "")
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


def test_soft_keep_with_structural_drift_withholds_unsafe(publication_repo: str):
    retained = "# Existing English\n"
    Path(publication_repo, "ydb/docs/en/a.md").write_text(retained, encoding="utf-8")
    subprocess.run(["git", "add", "ydb/docs/en/a.md"], cwd=publication_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "retained EN"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    result = _pair_result(target_text=retained)
    result.pair_results[0].source_text = "# Источник\n\n## Обязательный раздел\n"
    result.pair_results[0].soft_keep_reason = "translation timed out"

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == "WITHHOLD_UNSAFE"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "failed"


def test_soft_keep_without_publishable_git_diff_is_hard_no_artifact_failure(
    publication_repo: str,
):
    retained = "Hello.\n"
    result = _pair_result(target_text=retained)
    result.pair_results[0].soft_keep_reason = "translation timed out"

    job, gh, _prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
        commit_succeeds=False,
    )

    assert commit.call_count == 1
    assert job.pr_result.publication_impact == "WITHHOLD_INCOMPLETE"
    assert job.pr_result.publication_failure == "no_publishable_artifact"
    assert job.blocked is True
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    source_summary = gh.post_issue_comment.call_args.args[3]
    assert "no_publishable_artifact" in source_summary
    assert finish.call_args.kwargs["status"] == "failed"
    assert job_requires_nonzero_exit(job) is True


def test_soft_keep_post_reconciliation_hash_uses_exact_published_bytes(
    publication_repo: str,
):
    original = "Hello.\n"
    repaired = "Hello after deterministic repair.\n"
    result = _pair_result(target_text=original)
    result.pair_results[0].soft_keep_reason = "translation timed out"

    def repair_on_disk(repo_path, *_args, **_kwargs):
        Path(repo_path, "ydb/docs/en/a.md").write_text(repaired, encoding="utf-8")
        return ["ydb/docs/en/a.md"]

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        reconcile_side_effect=repair_on_disk,
    )

    blocker = next(
        item
        for item in job.pr_result.final_tree_blockers
        if item.code == "translation_soft_keep"
    )
    assert blocker.artifact_sha256 == hashlib.sha256(repaired.encode()).hexdigest()
    assert blocker.artifact_sha256 != hashlib.sha256(original.encode()).hexdigest()
    parsed = parse_final_tree_blocker_manifest(gh.create_pull.call_args.kwargs["body"])
    assert parsed == [blocker]


def test_soft_keep_and_safe_en_link_target_publish_one_draft_red(
    publication_repo: str,
):
    retained = "Existing reviewed English.\n"
    Path(publication_repo, "ydb/docs/en/a.md").write_text(retained, encoding="utf-8")
    subprocess.run(["git", "add", "ydb/docs/en/a.md"], cwd=publication_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "retained link EN"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    result = _pair_result(target_text=retained)
    result.pair_results[0].source_text = "Обновлённый русский текст.\n"
    result.pair_results[0].soft_keep_reason = "translation timed out"
    impact_path = "ydb/docs/en/impact-soft-keep.md"
    Path(publication_repo, impact_path).write_text(
        "See [missing](missing.md).\n",
        encoding="utf-8",
    )

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        impact_path=impact_path,
    )

    assert job.pr_result.publication_impact == "PUBLISH_RED"
    body = gh.create_pull.call_args.kwargs["body"]
    parsed = parse_final_tree_blocker_manifest(body)
    assert {blocker.code for blocker in parsed} == {
        "translation_soft_keep",
        "en_link_target",
    }
    assert gh.create_pull.call_args.kwargs["draft"] is True


@pytest.mark.parametrize(
    "blocking",
    [
        "href_parity: missing auth_config.md#security-auth",
        "anchor_parity: RU/EN explicit {#id} differ",
    ],
)
def test_final_link_blocker_keeps_related_link_heuristic_repairable(blocking: str):
    result = _pair_result(target_text="Translated.\n", blocking=[blocking])
    result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/security/authentication.md",
            code="en_link_target",
            message="en_link_target: authentication.md: missing fragment",
        )
    ]

    assert evaluate_publication_impact(result) == PublicationImpact.PUBLISH_RED


def test_soft_keep_never_overrides_unrelated_unsafe_blocker(publication_repo: str):
    result = _pair_result(
        target_text="Hello.\n",
        blocking=["include_target: missing required include"],
    )
    result.pair_results[0].soft_keep_reason = "translation timed out"

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == "WITHHOLD_UNSAFE"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


def test_soft_keep_with_blocked_critic_withholds_unsafe(publication_repo: str):
    result = _pair_result(target_text="Hello.\n")
    run = result.pair_results[0]
    run.soft_keep_reason = "translation timed out"
    assert run.file_result is not None
    run.file_result.critic_unresolved = CriticResponse(
        verdict="blocked",
        issues=[
            CriticIssueOut(
                category="accuracy",
                severity="blocked",
                description="Retained prose does not reflect current RU",
                comment="Manual rewrite required",
            )
        ],
    )

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == "WITHHOLD_UNSAFE"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


@pytest.mark.parametrize(
    ("source_text", "retained_text"),
    [
        ("# Заголовок {#source-anchor}\n", "# Heading {#wrong-anchor}\n"),
        (
            "# Ссылка\n\n[Документация](https://yandex.cloud/ru/docs/ydb)\n",
            "# Link\n\n[Documentation](https://yandex.cloud/ru/docs/ydb)\n",
        ),
    ],
    ids=["explicit_heading_anchor_drift", "ru_locale_link"],
)
def test_soft_keep_ordinary_deterministic_blocker_withholds_unsafe(
    publication_repo: str,
    source_text: str,
    retained_text: str,
):
    target_path = "ydb/docs/en/a.md"
    Path(publication_repo, target_path).write_text(retained_text, encoding="utf-8")
    subprocess.run(["git", "add", target_path], cwd=publication_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "retained unsafe EN"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    result = _pair_result(target_text=retained_text)
    run = result.pair_results[0]
    run.source_text = source_text
    run.soft_keep_reason = "translation timed out"

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == PublicationImpact.WITHHOLD_UNSAFE
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        [
            {
                "path": "ydb/docs/en/a.md",
                "code": "translation_soft_keep",
                "message": "manual repair required",
            }
        ],
        [
            {
                "path": "ydb/docs/en/a.md",
                "code": "unknown_repairable_code",
                "message": "manual repair required",
                "artifact_sha256": "0" * 64,
            }
        ],
    ],
)
def test_soft_keep_manifest_missing_hash_or_unknown_code_fails_closed(payload):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    body = f"<!-- ydbdoc-final-tree-blockers:v2:{encoded} -->"

    with pytest.raises(ValueError, match="malformed final-tree blocker manifest"):
        parse_final_tree_blocker_manifest(body)


def test_soft_keep_reports_only_failed_path_and_manual_doc_verify_action():
    result = _pair_result(target_text="Translated clean page.\n")
    auth_path = "ydb/docs/en/core/security/authentication.md"
    auth_pair = DocPair(
        ru_path=auth_path.replace("/en/", "/ru/"),
        en_path=auth_path,
        ru_changed=True,
    )
    retained = "# Authentication\n\nExisting reviewed English.\n"
    result.pair_results.append(
        PairRunResult(
            plan=PairPlan(
                pair=auth_pair,
                action="translate_to_en",
                source_path=auth_pair.ru_path,
                target_path=auth_path,
                source_lang="ru",
                target_lang="en",
            ),
            target_text=retained,
            source_text="# Аутентификация\n\nНовый текст.\n",
            soft_keep_reason="Invalid JSON in LLM response",
            file_result=FileTranslationResult(
                file_path=auth_path,
                final_text=retained,
                segments_count=0,
                verdict="warnings",
                prompt_version="soft-keep",
            ),
        )
    )
    result.final_tree_blockers = [_soft_keep_blocker(auth_path, retained)]
    result.publication_impact = PublicationImpact.PUBLISH_RED
    cfg = load_config(env=_env())

    source = append_retention_footer(
        build_source_pr_comment(
            result,
            translation_pr_number=99,
            meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
            config=cfg,
            committed=True,
        )
    )
    report = build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=cfg,
    )
    commit_message = build_commit_message(7, result, config=cfg)

    for body in (source, report):
        assert auth_path in body
        assert "Invalid JSON in LLM response" in body
        assert "вручную обновить EN" in body
        assert "doc_verify" in body
        assert "doc_continue" not in body
    assert "- 🟢 `ydb/docs/en/core/security/authentication.md`" not in report
    assert "Translated 1 files" in commit_message
    assert auth_path not in commit_message


def test_structurally_safe_real_translation_publishes_broken_target_as_draft_red(
    publication_repo: str,
):
    repo = Path(publication_repo)
    href = "core/target.md#missing-fragment"
    source_text = f"См. [цель]({href}).\n"
    target_text = f"See [target]({href}).\n"
    assert check_href_parity(source_text, target_text) == []
    (repo / "ydb/docs/ru/core").mkdir(parents=True, exist_ok=True)
    (repo / "ydb/docs/ru/core/target.md").write_text(
        "# Цель\n",
        encoding="utf-8",
    )
    (repo / "ydb/docs/en/core/target.md").write_text(
        "# Target\n",
        encoding="utf-8",
    )
    (repo / "ydb/docs/en/core/toc_p.yaml").write_text(
        "items:\n"
        "- name: A\n"
        "  href: ../a.md\n"
        "- name: Target\n"
        "  href: target.md\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "target fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "ydb/docs/ru/a.md").write_text(source_text, encoding="utf-8")

    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    gh = MagicMock()
    gh.get_pull.return_value = pull
    gh.get_branch_sha.return_value = None
    gh.find_open_pull_by_head.return_value = None
    gh.create_pull.return_value = ("https://github.com/o/r/pull/99", 99, True)
    gh.iter_issue_comments.return_value = iter(())
    gh.post_issue_comment.return_value = "https://github.com/o/r/pull/7#issuecomment-1"

    def fake_file_result(_harness, _state, _ctx):
        return FileTranslationResult(
            file_path="ydb/docs/en/a.md",
            final_text=target_text,
            segments_count=1,
            verdict="ok",
            prompt_version="test",
        )

    with ExitStack() as stack:
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh)
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=[("ydb/docs/ru/a.md", "modified")],
            )
        )
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[])
        )
        stack.enter_context(
            patch("ydbdoc_review.harness.pair.FileHarness.run", fake_file_result)
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
                return_value=[],
            )
        )
        prepare = stack.enter_context(
            patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base")
        )
        commit = stack.enter_context(
            patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True)
        )
        push = stack.enter_context(patch("ydbdoc_review.github.workflow.push_branch"))
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.run_doc_verify",
                return_value=SimpleNamespace(
                    translation_comment_url="https://github.com/o/r/pull/99#issuecomment-2",
                    pr_result=PRTranslationResult(),
                ),
            )
        )
        job = run_doc_translate(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
        )

    run = job.pr_result.pair_results[0]
    assert run.error is None
    assert run.validation_issues == ()
    assert run.file_result is not None
    assert run.file_result.segment_alignment_error is None
    assert run.file_result.manual_actions == []
    assert all(
        blocker.startswith("en_link_target:")
        for blocker in run.file_result.heuristic_blocking
    )
    assert job.pr_result.publication_impact == "PUBLISH_RED"
    assert [(blocker.path, blocker.code) for blocker in job.pr_result.final_tree_blockers] == [
        ("ydb/docs/en/a.md", "en_link_target")
    ]
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert gh.create_pull.call_args.kwargs["draft"] is True
    assert "QA RED, do not merge" in gh.create_pull.call_args.kwargs["body"]
    assert job_requires_nonzero_exit(job) is False


def test_missing_red_pr_is_hard_failure_and_not_false_green(publication_repo: str):
    result = _pair_result(target_text="See [missing](missing.md).\n")

    job, gh, _prepare, _commit, _push, finish = _run_top_level(
        publication_repo,
        result,
        create_succeeds=False,
    )

    assert job.translation_pr_number is None
    assert job_requires_nonzero_exit(job) is True
    source_summary = gh.post_issue_comment.call_args.args[3]
    assert "translation PR **не создан**" in source_summary
    assert "🔴" in source_summary
    assert "перевод готов" not in source_summary
    assert finish.call_args.kwargs["status"] == "failed"


def test_final_tree_blocker_without_pair_survives_as_draft_red(publication_repo: str):
    impact_path = "ydb/docs/en/impact.md"
    Path(publication_repo, impact_path).write_text(
        "Redirected page links to [missing](gone.md).\n",
        encoding="utf-8",
    )
    result = _pair_result()

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
        impact_path=impact_path,
    )

    assert all(run.plan.target_path != impact_path for run in job.pr_result.pair_results)
    blockers = getattr(job.pr_result, "final_tree_blockers", ())
    assert [(b.path, b.code) for b in blockers] == [(impact_path, "en_link_target")]
    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_RED"
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert gh.create_pull.call_args.kwargs["draft"] is True
    assert job_requires_nonzero_exit(job) is False


def test_real_git_commit_preserves_impact_blocker_through_inline_verify(
    publication_repo: str,
    tmp_path: Path,
):
    import ydbdoc_review.github.workflow as workflow

    impact_path = "ydb/docs/en/impact.md"
    Path(publication_repo, impact_path).write_text(
        "Existing impact page.\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", impact_path],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add existing impact page"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    upstream = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(upstream)],
        cwd=publication_repo,
        check=True,
    )
    subprocess.run(
        ["git", "fetch", "origin", "main:refs/remotes/origin/feature/docs"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    upstream_base_sha = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    Path(publication_repo, "source-checkout-only.txt").write_text(
        "must not become the translation base\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "source-checkout-only.txt"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "source checkout diverges from upstream"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    source_checkout_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert source_checkout_sha != upstream_base_sha
    Path(publication_repo, impact_path).write_text(
        "See [missing](gone.md).\n",
        encoding="utf-8",
    )
    translate_result = _pair_result(target_text="Translated.\n")
    verify_result = _pair_result(target_text="Translated.\n")

    source_pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = "Привет.\n"
    gh.iter_issue_comments.return_value = iter(())
    gh.find_open_pull_by_head.return_value = None
    gh.create_pull.return_value = ("https://github.com/o/r/pull/99", 99, True)
    gh.post_issue_comment.return_value = "comment-url"

    translate_changes = [("ydb/docs/ru/a.md", "modified")]
    verify_changes = [
        ("ydb/docs/en/a.md", "modified"),
        (impact_path, "modified"),
    ]
    git_change_calls = iter((translate_changes, verify_changes, verify_changes))

    def _api_changes(_gh, _owner, _repo, number):
        return translate_changes if number == 7 else verify_changes

    real_prepare = workflow.prepare_translation_branch_on_base
    prepare_calls = 0

    def _prepare_once(*args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_calls == 1:
            return real_prepare(*args, **kwargs)
        return None

    real_verify = workflow.run_doc_verify
    verify_jobs = []

    def _capture_real_verify(**kwargs):
        verify_job = real_verify(**kwargs)
        verify_jobs.append(verify_job)
        return verify_job

    ops_ctx = SimpleNamespace(recorder=None, continue_feedback=None)
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.finish_ops_job"),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            side_effect=lambda *_args, **_kwargs: next(git_change_calls),
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=_api_changes,
        ),
        patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            return_value=translate_result,
        ),
        patch(
            "ydbdoc_review.github.workflow._run_verify_pairs",
            return_value=verify_result,
        ),
        patch(
            "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
            return_value=[],
        ),
        patch(
            "ydbdoc_review.github.workflow._declare_exact_ascii_fragment_targets_after_apply",
            return_value=[impact_path],
        ),
        patch(
            "ydbdoc_review.github.workflow.translation_branch_base",
            return_value=(str(upstream), "main"),
        ),
        patch(
            "ydbdoc_review.github.workflow.prepare_translation_branch_on_base",
            side_effect=_prepare_once,
        ),
        patch("ydbdoc_review.github.workflow.push_branch"),
        patch(
            "ydbdoc_review.github.workflow.run_doc_verify",
            side_effect=_capture_real_verify,
        ),
    ):
        job = run_doc_translate(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
        )

    committed_impact = subprocess.run(
        ["git", "show", f"HEAD:{impact_path}"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    checked_out_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    translation_parent_sha = subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert committed_impact == "See [missing](gone.md).\n"
    assert checked_out_branch == "ydbdoc-review/pr-7"
    assert translation_parent_sha == upstream_base_sha
    assert translation_parent_sha != source_checkout_sha
    assert job.committed is True
    assert verify_jobs
    assert [(b.path, b.code) for b in verify_jobs[-1].pr_result.final_tree_blockers] == [
        (impact_path, "en_link_target")
    ]
    assert verify_jobs[-1].pr_result.publication_impact == "PUBLISH_RED"


def test_verify_empty_scoped_result_preserves_inherited_no_pair_blocker(
    publication_repo: str,
):
    blocker = FinalTreeBlocker(
        path="ydb/docs/en/impact.md",
        code="en_link_target",
        message="en_link_target: impact.md: missing target",
    )
    pull = {
        "title": "Auto-translate docs from PR #7",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    gh = MagicMock()
    gh.get_pull.return_value = pull

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_git", return_value=[]),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]),
    ):
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            inherited_final_tree_blockers=[blocker],
            skip_ops_gates=True,
        )

    assert job.pr_result.final_tree_blockers == [blocker]
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def test_standalone_verify_rescans_durable_no_pair_blocker_outside_source_scope(
    publication_repo: str,
):
    impact_path = "ydb/docs/en/impact.md"
    Path(publication_repo, impact_path).write_text(
        "See [missing](gone.md).\n",
        encoding="utf-8",
    )
    blocker = FinalTreeBlocker(
        path=impact_path,
        code="en_link_target",
        message="en_link_target: impact.md: missing target `gone.md`",
    )
    published_result = _pair_result()
    published_result.final_tree_blockers = [blocker]
    published_result.publication_impact = PublicationImpact.PUBLISH_RED
    body = build_translation_pr_body(
        7,
        "o/r",
        publication_result=published_result,
    )
    assert "ydbdoc-final-tree-blockers:v2" in body

    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": body,
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-sha",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    source_pull = {
        "title": "docs",
        "body": "",
        "head": {
            "ref": "docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = "Привет.\n"

    def _api_changes(_gh, _owner, _repo, number):
        if number == 7:
            return [("ydb/docs/ru/a.md", "modified")]
        return [("ydb/docs/en/a.md", "modified"), (impact_path, "modified")]

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/en/a.md", "modified"), (impact_path, "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=_api_changes,
        ),
        patch(
            "ydbdoc_review.github.workflow._run_verify_pairs",
            return_value=_pair_result(),
        ),
        patch(
            "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
            return_value=[],
        ),
    ):
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            skip_ops_gates=True,
        )

    assert [(item.path, item.code) for item in job.pr_result.final_tree_blockers] == [
        (impact_path, "en_link_target")
    ]
    assert job.pr_result.final_tree_blockers != [blocker]
    assert "missing file" in job.pr_result.final_tree_blockers[0].message
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def test_standalone_verify_keeps_deleted_durable_impact_path_as_tombstone(
    publication_repo: str,
):
    impact_path = "ydb/docs/en/impact.md"
    impact_file = Path(publication_repo, impact_path)
    impact_file.write_text("See [missing](gone.md).\n", encoding="utf-8")
    subprocess.run(["git", "add", impact_path], cwd=publication_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "durable impact baseline"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    blocker = FinalTreeBlocker(
        path=impact_path,
        code="en_link_target",
        message="en_link_target: impact.md: missing target `gone.md`",
    )
    published_result = _pair_result()
    published_result.final_tree_blockers = [blocker]
    published_result.publication_impact = PublicationImpact.PUBLISH_RED
    body = build_translation_pr_body(7, "o/r", publication_result=published_result)
    impact_file.unlink()

    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": body,
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-sha",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    source_pull = {
        "title": "docs",
        "body": "",
        "head": {
            "ref": "docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = "Привет.\n"

    def api_changes(_gh, _owner, _repo, number):
        if number == 7:
            return [("ydb/docs/ru/a.md", "modified")]
        return [("ydb/docs/en/a.md", "modified"), (impact_path, "deleted")]

    with patch(
        "ydbdoc_review.github.workflow.GitHubClient", return_value=gh
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/en/a.md", "modified"), (impact_path, "deleted")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        side_effect=api_changes,
    ), patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_pair_result(),
    ), patch(
        "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
        return_value=[],
    ):
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            skip_ops_gates=True,
        )

    assert impact_file.exists() is False
    assert job.pr_result.final_tree_blockers == [blocker]
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def _run_standalone_soft_keep_verify(
    publication_repo: str,
    *,
    current_text: str,
    verify_result: PRTranslationResult,
    translation_draft: bool = True,
    event_log: list[str] | None = None,
    no_commit: bool = True,
    ready_transition_after_push: bool = False,
    draft_conversion_fail_on_call: int | None = None,
):
    path = "ydb/docs/en/core/security/authentication.md"
    ru_path = path.replace("/en/", "/ru/")
    original = "# Authentication\n\nExisting reviewed English.\n"
    Path(publication_repo, path).parent.mkdir(parents=True, exist_ok=True)
    Path(publication_repo, ru_path).parent.mkdir(parents=True, exist_ok=True)
    Path(publication_repo, path).write_text(original, encoding="utf-8")
    Path(publication_repo, ru_path).write_text(
        "# Аутентификация\n\nОбновлённый русский текст.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=publication_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "soft-keep published artifact"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    Path(publication_repo, path).write_text(current_text, encoding="utf-8")
    blocker = _soft_keep_blocker(path, original)
    published = PRTranslationResult(
        final_tree_blockers=[blocker],
        publication_impact=PublicationImpact.PUBLISH_RED,
    )
    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": build_translation_pr_body(7, "o/r", publication_result=published),
        "draft": translation_draft,
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-sha",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    source_pull = {
        "title": "docs",
        "body": "",
        "head": {
            "ref": "docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = Path(publication_repo, ru_path).read_text(
        encoding="utf-8"
    )
    gh.get_branch_sha.return_value = "translation-sha"
    gh.post_issue_comment.return_value = "https://github.com/o/r/pull/99#issuecomment-1"
    convert_calls = 0

    def convert_to_draft(*_args, **_kwargs):
        nonlocal convert_calls
        convert_calls += 1
        if event_log is not None:
            event_log.append("draft")
        if draft_conversion_fail_on_call == convert_calls:
            raise GitHubAPIError("cannot confirm verify draft", status_code=403)
        translation_pull["draft"] = True
        return True

    gh.convert_pull_to_draft.side_effect = convert_to_draft
    gh.update_pull_body.side_effect = lambda *_args, **_kwargs: (
        event_log.append("body") if event_log is not None else None
    )

    def api_changes(_gh, _owner, _repo, number):
        return [(ru_path, "modified")] if number == 7 else [(path, "modified")]

    with ExitStack() as stack:
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh)
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=[(path, "modified")],
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                side_effect=api_changes,
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow._run_verify_pairs",
                return_value=verify_result,
            )
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
                return_value=[],
            )
        )
        if not no_commit:
            prepare = stack.enter_context(
                patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base")
            )
            prepare.side_effect = lambda *_args, **_kwargs: (
                event_log.append("prepare") if event_log is not None else None
            )
            commit = stack.enter_context(
                patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True)
            )
            commit.side_effect = lambda *_args, **_kwargs: (
                event_log.append("commit") if event_log is not None else None
            ) or True
            push = stack.enter_context(
                patch("ydbdoc_review.github.workflow.push_branch")
            )

            def push_verify(*_args, **_kwargs):
                if event_log is not None:
                    event_log.append("push")
                if ready_transition_after_push:
                    translation_pull["draft"] = False

            push.side_effect = push_verify
            stack.enter_context(
                patch("ydbdoc_review.github.workflow.git_head_sha", return_value="verify-sha")
            )
            rollback = stack.enter_context(
                patch("ydbdoc_review.github.workflow.rollback_pushed_branch")
            )
            rollback.side_effect = lambda *_args, **_kwargs: (
                event_log.append("rollback") if event_log is not None else None
            )
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            no_commit=no_commit,
            config=load_config(env=_env()),
            skip_ops_gates=True,
        )
    return job, gh, blocker


def test_soft_keep_manifest_survives_inline_verify_when_bytes_unchanged(
    publication_repo: str,
):
    path = "ydb/docs/en/core/security/authentication.md"
    retained = "# Authentication\n\nExisting reviewed English.\n"
    job, _gh, blocker = _run_standalone_soft_keep_verify(
        publication_repo,
        current_text=retained,
        verify_result=_verify_pair_result(path, retained),
    )

    assert job.pr_result.final_tree_blockers == [blocker]
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def test_verify_with_unresolved_soft_keep_converts_ready_pr_back_to_draft(
    publication_repo: str,
):
    path = "ydb/docs/en/core/security/authentication.md"
    retained = "# Authentication\n\nExisting reviewed English.\n"
    events: list[str] = []
    job, gh, blocker = _run_standalone_soft_keep_verify(
        publication_repo,
        current_text=retained,
        verify_result=_verify_pair_result(path, retained),
        translation_draft=False,
        event_log=events,
    )

    assert job.pr_result.final_tree_blockers == [blocker]
    assert job.pr_result.publication_impact == PublicationImpact.PUBLISH_RED
    assert gh.convert_pull_to_draft.call_args_list == [
        (("o", "r", 99), {}),
        (("o", "r", 99), {}),
    ]
    assert events == ["draft", "draft", "body"]


def test_verify_clears_soft_keep_but_keeps_red_body_when_other_pair_is_unsafe(
    publication_repo: str,
):
    path = "ydb/docs/en/core/security/authentication.md"
    repaired = "# Authentication\n\nManually repaired English text.\n"
    verify_result = _verify_pair_result(path, repaired)
    verify_result.pair_results.extend(
        _pair_result(
            target_text="Unsafe other page.\n",
            blocking=["anchor_parity: explicit heading anchor changed"],
        ).pair_results
    )
    events: list[str] = []

    job, gh, blocker = _run_standalone_soft_keep_verify(
        publication_repo,
        current_text=repaired,
        verify_result=verify_result,
        translation_draft=False,
        event_log=events,
    )

    assert blocker not in job.pr_result.final_tree_blockers
    assert job.pr_result.publication_impact == PublicationImpact.WITHHOLD_UNSAFE
    assert gh.convert_pull_to_draft.call_args_list == [
        (("o", "r", 99), {}),
        (("o", "r", 99), {}),
    ]
    body = gh.update_pull_body.call_args.args[3]
    assert "QA RED, do not merge" in body
    assert "translation_soft_keep" not in body
    assert events == ["draft", "draft", "body"]


def test_verify_redrafts_ready_transition_before_red_body_after_branch_push(
    publication_repo: str,
):
    path = "ydb/docs/en/core/security/authentication.md"
    repaired = "# Authentication\n\nManually repaired English text.\n"
    verify_result = _verify_pair_result(path, repaired)
    verify_result.pair_results.extend(
        _pair_result(
            target_text="Unsafe changed other page.\n",
            blocking=["anchor_parity: explicit heading anchor changed"],
        ).pair_results
    )
    events: list[str] = []

    job, gh, _blocker = _run_standalone_soft_keep_verify(
        publication_repo,
        current_text=repaired,
        verify_result=verify_result,
        translation_draft=False,
        event_log=events,
        no_commit=False,
        ready_transition_after_push=True,
    )

    assert job.pushed is True
    assert events == ["draft", "prepare", "commit", "push", "draft", "body"]
    assert gh.convert_pull_to_draft.call_count == 2


def test_verify_post_push_redraft_failure_rolls_back_before_body(
    publication_repo: str,
):
    path = "ydb/docs/en/core/security/authentication.md"
    repaired = "# Authentication\n\nManually repaired English text.\n"
    verify_result = _verify_pair_result(path, repaired)
    verify_result.pair_results.extend(
        _pair_result(
            target_text="Unsafe changed other page.\n",
            blocking=["anchor_parity: explicit heading anchor changed"],
        ).pair_results
    )
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot confirm verify draft"):
        _run_standalone_soft_keep_verify(
            publication_repo,
            current_text=repaired,
            verify_result=verify_result,
            translation_draft=False,
            event_log=events,
            no_commit=False,
            ready_transition_after_push=True,
            draft_conversion_fail_on_call=2,
        )

    assert events == [
        "draft",
        "prepare",
        "commit",
        "push",
        "draft",
        "rollback",
    ]


def test_standalone_verify_clears_soft_keep_after_changed_green_pair(
    publication_repo: str,
):
    path = "ydb/docs/en/core/security/authentication.md"
    repaired = "# Authentication\n\nManually repaired English text.\n"
    job, gh, _blocker = _run_standalone_soft_keep_verify(
        publication_repo,
        current_text=repaired,
        verify_result=_verify_pair_result(path, repaired),
    )

    assert job.pr_result.final_tree_blockers == []
    assert job.pr_result.publication_impact == "PUBLISH_NORMAL"
    gh.update_pull_body.assert_called_once()
    updated_body = gh.update_pull_body.call_args.args[3]
    assert "translation_soft_keep" not in updated_body
    gh.convert_pull_to_draft.assert_not_called()


@pytest.mark.parametrize("verified", [False, True])
def test_standalone_verify_keeps_changed_soft_keep_if_unverified_or_unsafe(
    publication_repo: str,
    verified: bool,
):
    path = "ydb/docs/en/core/security/authentication.md"
    repaired = "# Authentication\n\nChanged but not proven safe.\n"
    verify_result = (
        _verify_pair_result(path, repaired, unsafe=True)
        if verified
        else PRTranslationResult()
    )
    job, _gh, blocker = _run_standalone_soft_keep_verify(
        publication_repo,
        current_text=repaired,
        verify_result=verify_result,
    )

    assert blocker in job.pr_result.final_tree_blockers
    assert job.pr_result.publication_impact in {
        PublicationImpact.PUBLISH_RED,
        PublicationImpact.WITHHOLD_UNSAFE,
    }


def test_verify_critic_fix_recursion_preserves_inherited_no_pair_blocker(
    publication_repo: str,
):
    blocker = FinalTreeBlocker(
        path="ydb/docs/en/impact.md",
        code="en_link_target",
        message="en_link_target: impact.md: missing target",
    )
    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-sha",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    source_pull = {
        "title": "docs",
        "head": {
            "ref": "docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = "Привет.\n"
    gh.iter_issue_comments.return_value = iter(())
    gh.post_issue_comment.return_value = "comment-url"

    def _api_changes(_gh, _owner, _repo, number):
        if number == 7:
            return [("ydb/docs/ru/a.md", "modified")]
        return [("ydb/docs/en/a.md", "modified")]

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/en/a.md", "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=_api_changes,
        ),
        patch(
            "ydbdoc_review.github.workflow._run_verify_pairs",
            side_effect=[
                _pair_result(target_text="Critic fix one.\n"),
                _pair_result(target_text="Critic fix two.\n"),
            ],
        ),
        patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"),
        patch(
            "ydbdoc_review.github.workflow.git_commit_paths",
            side_effect=[True, False],
        ),
        patch("ydbdoc_review.github.workflow.push_branch"),
        patch(
            "ydbdoc_review.github.workflow.git_head_sha",
            side_effect=[
                "checkout-one",
                "before-one",
                "after-one",
                "checkout-two",
                "before-two",
            ],
        ),
        patch(
            "ydbdoc_review.github.workflow._enforce_report_checkout_bytes",
            return_value=[],
        ),
    ):
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
            inherited_final_tree_blockers=[blocker],
            skip_ops_gates=True,
        )

    assert job.pr_result.final_tree_blockers == [blocker]
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def test_existing_ready_translation_pr_is_converted_to_draft(publication_repo: str):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        existing_pr=True,
        event_log=events,
    )

    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_RED"
    gh.update_pull_body.assert_called_once()
    assert "QA RED, do not merge" in gh.update_pull_body.call_args.args[3]
    gh.convert_pull_to_draft.assert_called_once_with("o", "r", 99)
    assert events == ["discover", "draft", "push", "refetch", "body"]
    gh.create_pull.assert_not_called()


def test_existing_ready_pr_conversion_failure_leaves_remote_untouched(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            result,
            existing_pr=True,
            event_log=events,
            draft_conversion_fails=True,
        )

    assert events == ["discover", "draft"]


def test_existing_red_pr_ready_transition_after_push_is_converted_again(
    publication_repo: str,
):
    events: list[str] = []

    _job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        _pair_result(target_text="See [missing](missing.md).\n"),
        existing_pr=True,
        remote_branch_exists=True,
        remote_branch_sha="previous-sha",
        postpush_pr_draft=False,
        event_log=events,
    )

    assert events == ["discover", "draft", "push", "refetch", "draft", "body"]
    assert gh.convert_pull_to_draft.call_count == 2


def test_existing_red_pr_postpush_reconversion_failure_rolls_back_before_body(
    publication_repo: str,
):
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            _pair_result(target_text="See [missing](missing.md).\n"),
            existing_pr=True,
            remote_branch_exists=True,
            remote_branch_sha="previous-sha",
            postpush_pr_draft=False,
            draft_conversion_fail_on_call=2,
            event_log=events,
        )

    assert events == ["discover", "draft", "push", "refetch", "draft", "rollback"]


def test_red_pr_transport_failure_rolls_back_and_preserves_original_exception(
    publication_repo: str,
):
    events: list[str] = []
    transport_error = Timeout("draft confirmation timed out")

    with pytest.raises(Timeout, match="draft confirmation timed out") as raised:
        _run_top_level(
            publication_repo,
            _pair_result(target_text="See [missing](missing.md).\n"),
            existing_pr=True,
            remote_branch_exists=True,
            remote_branch_sha="previous-sha",
            postpush_confirmation_error=transport_error,
            event_log=events,
        )

    assert raised.value is transport_error
    assert events == ["discover", "draft", "push", "refetch", "rollback"]


def test_red_pr_confirmation_and_rollback_failures_preserve_both_exceptions(
    publication_repo: str,
):
    events: list[str] = []
    transport_error = Timeout("draft confirmation timed out")
    rollback_error = RuntimeError("guarded rollback failed")

    with pytest.raises(ExceptionGroup) as raised:
        _run_top_level(
            publication_repo,
            _pair_result(target_text="See [missing](missing.md).\n"),
            existing_pr=True,
            remote_branch_exists=True,
            remote_branch_sha="previous-sha",
            postpush_confirmation_error=transport_error,
            rollback_error=rollback_error,
            event_log=events,
        )

    assert raised.value.exceptions == (transport_error, rollback_error)
    assert events == ["discover", "draft", "push", "refetch", "rollback"]


def test_red_pr_confirmation_does_not_catch_system_exit(publication_repo: str):
    events: list[str] = []

    with pytest.raises(SystemExit, match="stop now"):
        _run_top_level(
            publication_repo,
            _pair_result(target_text="See [missing](missing.md).\n"),
            existing_pr=True,
            remote_branch_exists=True,
            remote_branch_sha="previous-sha",
            postpush_confirmation_error=SystemExit("stop now"),
            event_log=events,
        )

    assert events == ["discover", "draft", "push", "refetch"]


def test_late_existing_red_pr_is_converted_before_body_mutation(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    _job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        late_existing_pr=True,
        event_log=events,
    )

    assert events == ["discover", "create", "draft", "push", "refetch", "body"]
    gh.convert_pull_to_draft.assert_called_once_with("o", "r", 99)


def test_late_existing_red_pr_conversion_failure_does_not_mutate_body(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            result,
            late_existing_pr=True,
            event_log=events,
            draft_conversion_fails=True,
        )

    assert events == ["discover", "create", "draft"]


def test_post_push_existing_pr_fallback_converts_before_body_mutation(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    _run_top_level(
        publication_repo,
        result,
        late_existing_pr=True,
        remote_branch_exists=False,
        postpush_pr_draft=False,
        event_log=events,
    )

    assert events == ["discover", "push", "create", "refetch", "draft", "body"]


def test_post_push_existing_pr_fallback_conversion_failure_is_fail_closed(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            result,
            late_existing_pr=True,
            remote_branch_exists=False,
            postpush_pr_draft=False,
            event_log=events,
            draft_conversion_fails=True,
        )

    assert events == [
        "discover",
        "push",
        "create",
        "refetch",
        "draft",
        "rollback",
    ]


def test_stale_remote_branch_without_diff_falls_through_to_post_push_draft_creation(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        remote_branch_exists=True,
        prepush_create_returns_none=True,
        event_log=events,
    )

    assert events == ["discover", "create", "push", "create", "refetch"]
    assert job.translation_pr_number == 99
    assert [call.kwargs["draft"] for call in gh.create_pull.call_args_list] == [True, True]


def test_red_push_lease_failure_does_not_mutate_pull_request(
    publication_repo: str,
):
    events: list[str] = []

    with pytest.raises(RuntimeError, match="lease failed"):
        _run_top_level(
            publication_repo,
            _pair_result(target_text="See [missing](missing.md).\n"),
            remote_branch_exists=False,
            event_log=events,
            push_fails=True,
        )

    assert events == ["discover", "push"]


def test_failed_post_push_draft_conversion_deletes_new_red_remote_ref(
    publication_repo: str,
    tmp_path: Path,
):
    upstream = tmp_path / "rollback-upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    Path(publication_repo, "ydb/docs/en/a.md").write_text(
        "See [missing](missing.md).\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "ydb/docs/en/a.md"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "red candidate"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    red_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    events: list[str] = []
    rollback_result = _pair_result(target_text="See [missing](missing.md).\n")
    rollback_result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="en_link_target: a.md: missing target",
        )
    ]

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            rollback_result,
            late_existing_pr=True,
            remote_branch_exists=False,
            postpush_pr_draft=False,
            event_log=events,
            draft_conversion_fails=True,
            real_push_remote=str(upstream),
        )

    remote_ref = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", "refs/heads/ydbdoc-review/pr-7"],
        capture_output=True,
        text=True,
    )
    assert remote_ref.returncode != 0
    assert red_sha not in remote_ref.stdout
    assert events == ["discover", "push", "create", "refetch", "draft"]


def test_failed_post_push_conversion_restores_remote_only_previous_sha(
    publication_repo: str,
    tmp_path: Path,
):
    upstream = tmp_path / "restore-upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    writer = tmp_path / "remote-writer"
    subprocess.run(
        ["git", "clone", str(upstream), str(writer)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "writer@example.com"],
        cwd=writer,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "remote writer"],
        cwd=writer,
        check=True,
    )
    Path(writer, "remote-only.txt").write_text("old remote tip\n", encoding="utf-8")
    subprocess.run(["git", "add", "remote-only.txt"], cwd=writer, check=True)
    subprocess.run(
        ["git", "commit", "-m", "remote-only old tip"],
        cwd=writer,
        check=True,
        capture_output=True,
    )
    previous_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=writer,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch_ref = "refs/heads/ydbdoc-review/pr-7"
    subprocess.run(
        ["git", "push", str(upstream), f"HEAD:{branch_ref}"],
        cwd=writer,
        check=True,
        capture_output=True,
    )
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{previous_sha}^{{commit}}"],
            cwd=publication_repo,
            capture_output=True,
        ).returncode
        != 0
    )
    Path(publication_repo, "ydb/docs/en/a.md").write_text(
        "See [missing](missing.md).\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "ydb/docs/en/a.md"],
        cwd=publication_repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "red candidate"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    rollback_result = _pair_result(target_text="See [missing](missing.md).\n")
    rollback_result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="en_link_target: a.md: missing target",
        )
    ]

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            rollback_result,
            late_existing_pr=True,
            remote_branch_exists=True,
            remote_branch_sha=previous_sha,
            prepush_create_returns_none=True,
            postpush_pr_draft=False,
            draft_conversion_fails=True,
            real_push_remote=str(upstream),
        )

    restored_sha = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", branch_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert restored_sha == previous_sha


def _withhold_case(case: str) -> PRTranslationResult:
    if case == "pair_error":
        return _pair_result(target_text=None, error="translation failed")
    if case == "missing_expected_output":
        return _pair_result(target_text=None)
    if case == "translate_soft_keep":
        return _pair_result(
            target_text="Hello.\n",
            warnings=["translate_soft_keep: translate failed; kept tip EN unchanged"],
        )
    if case == "source_retaining_manual_action":
        return _pair_result(
            manual_actions=[ManualAction("s0001", "paragraph 1", "translate manually")]
        )
    if case == "segment_alignment":
        return _pair_result(alignment_error="expected 2 segments, got 1")
    if case == "deterministic_integrity":
        return _pair_result(
            link_issues=(
                LinkContractIssue("ambiguous_link_slot", "cannot prove source-owned slot"),
            )
        )
    if case == "include_parity":
        result = _pair_result(
            target_text="# Page\n\nTranslated body.\n",
            blocking=["include_parity: cannot auto-insert EN include"],
        )
        result.pair_results[0].source_text = (
            "# Страница\n\nТекст.\n\n"
            "{% include [note](./core/_includes/note.md) %}\n"
        )
        return result
    if case == "heading_parity":
        result = _pair_result(
            target_text="# Page\n\nTranslated body.\n",
            blocking=["heading_parity: source 2 headings vs target 1"],
        )
        result.pair_results[0].source_text = "# Страница\n\n## Раздел\n\nТекст.\n"
        return result
    if case == "list_tab_parity":
        result = _pair_result(
            target_text="# Page\n\nTranslated body.\n",
            blocking=["list_tab_parity: source 1 tab blocks vs target 0"],
        )
        result.pair_results[0].source_text = (
            "# Страница\n\n{% list tabs %}\n\n- Вкладка\n\n  Текст.\n\n"
            "{% endlist %}\n"
        )
        return result
    if case == "protect_marker_leakage":
        return _pair_result(
            target_text="Leaked ⟦C1⟧ marker.\n",
            blocking=["unrestored_placeholder: 1 leftover protect marker in EN"],
        )
    if case == "link_wrapper_loss":
        return _pair_result(
            link_issues=(
                LinkContractIssue("missing_link_wrapper", "source link wrapper was lost"),
            )
        )
    if case == "invalid_navigation_yaml":
        result = _pair_result()
        result.navigation_results = [
            NavigationRunResult(
                ru_path="ydb/docs/ru/core/toc_p.yaml",
                en_path="ydb/docs/en/core/toc_p.yaml",
                kind="toc",
                target_text="items: [",
                warnings=["invalid_yaml: expected closing bracket"],
                verdict="blocked",
            )
        ]
        return result
    raise AssertionError(f"unknown case: {case}")


@pytest.mark.parametrize(
    ("case", "expected_impact"),
    [
        ("pair_error", "WITHHOLD_INCOMPLETE"),
        ("missing_expected_output", "WITHHOLD_INCOMPLETE"),
        ("source_retaining_manual_action", "WITHHOLD_INCOMPLETE"),
        ("segment_alignment", "WITHHOLD_UNSAFE"),
        ("deterministic_integrity", "WITHHOLD_UNSAFE"),
        ("include_parity", "WITHHOLD_UNSAFE"),
        ("heading_parity", "WITHHOLD_UNSAFE"),
        ("list_tab_parity", "WITHHOLD_UNSAFE"),
        ("protect_marker_leakage", "WITHHOLD_UNSAFE"),
        ("link_wrapper_loss", "WITHHOLD_UNSAFE"),
        ("invalid_navigation_yaml", "WITHHOLD_UNSAFE"),
    ],
)
def test_withhold_never_prepares_or_publishes(
    publication_repo: str,
    case: str,
    expected_impact: str,
):
    result = _withhold_case(case)

    job, gh, prepare, commit, push, finish = _run_top_level(publication_repo, result)

    assert getattr(job.pr_result, "publication_impact", None) == expected_impact
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "failed"
    assert job_requires_nonzero_exit(job) is True


@pytest.mark.parametrize(
    ("case", "soft_keep", "expected_impact"),
    [
        ("pair_error", False, "WITHHOLD_INCOMPLETE"),
        ("missing_expected_output", False, "WITHHOLD_INCOMPLETE"),
        ("include_parity", False, "WITHHOLD_UNSAFE"),
        ("link_wrapper_loss", False, "WITHHOLD_UNSAFE"),
        ("include_parity", True, "WITHHOLD_UNSAFE"),
    ],
)
def test_withhold_precedence_always_dominates_broken_link_red(
    publication_repo: str,
    case: str,
    soft_keep: bool,
    expected_impact: str,
):
    result = _withhold_case(case)
    if soft_keep:
        Path(publication_repo, "ydb/docs/en/a.md").write_text(
            result.pair_results[0].target_text or "",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "ydb/docs/en/a.md"],
            cwd=publication_repo,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "soft-keep retained target"],
            cwd=publication_repo,
            check=True,
            capture_output=True,
        )
        result.pair_results[0].soft_keep_reason = "translation failed"
        file_result = result.pair_results[0].file_result
        assert file_result is not None
        file_result.heuristic_warnings.append(
            "translate_soft_keep: translate failed; kept tip EN unchanged"
        )
    result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="en_link_target: a.md: missing target",
        )
    ]

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == expected_impact
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "failed"
    assert job_requires_nonzero_exit(job) is True


@pytest.mark.parametrize(
    "blocking_message",
    [
        "Кириллица в EN-тексте (строка ~1): «остаток»",
        "include_target: missing include target.md",
        "glossary_violation: expected YDB term",
        "inbound_fragment: missing stable fragment",
        "outbound_fragment: target fragment is absent",
        "href_parity: source/target href mismatch",
        "md_link_parity: source/target link mismatch",
    ],
)
def test_non_repairable_blocking_finding_withholds_even_with_final_link_blocker(
    publication_repo: str,
    blocking_message: str,
):
    result = _pair_result(blocking=[blocking_message])
    result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="en_link_target: a.md: missing target",
        )
    ]
    assert result_has_blocking_findings(result) is True

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == "WITHHOLD_UNSAFE"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


def test_unresolved_blocked_critic_withholds_even_with_final_link_blocker(
    publication_repo: str,
):
    result = _pair_result()
    file_result = result.pair_results[0].file_result
    assert file_result is not None
    file_result.critic_unresolved = CriticResponse(
        verdict="blocked",
        issues=[
            CriticIssueOut(
                segment_id="s0001",
                severity="blocked",
                category="accuracy",
                comment="Meaning is not preserved",
            )
        ],
    )
    result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="en_link_target: a.md: missing target",
        )
    ]
    assert result_has_blocking_findings(result) is True

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    assert job.pr_result.publication_impact == "WITHHOLD_UNSAFE"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()


def test_clean_candidate_keeps_normal_publication(publication_repo: str):
    result = _pair_result()

    job, gh, prepare, commit, push, finish = _run_top_level(publication_repo, result)

    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_NORMAL"
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert gh.create_pull.call_args.kwargs["draft"] is False
    assert "QA RED, do not merge" not in gh.create_pull.call_args.kwargs["body"]
    assert finish.call_args.kwargs["status"] == "ok"
    assert job_requires_nonzero_exit(job) is False


@pytest.mark.parametrize(
    ("remote_branch_exists", "remote_sha"),
    [(True, "manual-concurrent-sha"), (False, None)],
)
def test_normal_publication_uses_exact_remote_lease_for_existing_and_new_branch(
    publication_repo: str,
    remote_branch_exists: bool,
    remote_sha: str | None,
):
    job, gh, _prepare, _commit, push, _finish = _run_top_level(
        publication_repo,
        _pair_result(),
        remote_branch_exists=remote_branch_exists,
        remote_branch_sha=remote_sha,
    )

    assert job.pr_result.publication_impact == "PUBLISH_NORMAL"
    gh.get_branch_sha.assert_called_once_with("o", "r", "ydbdoc-review/pr-7")
    assert push.call_args.kwargs["guard_remote_ref"] is True
    assert push.call_args.kwargs["expected_remote_sha"] == remote_sha


def test_clean_empty_scope_keeps_existing_success_without_pr(publication_repo: str):
    result = PRTranslationResult()

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
        source_changes=[],
    )

    assert job.pr_result.publication_impact == "PUBLISH_NORMAL"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "ok"
    assert job_requires_nonzero_exit(job) is False


def test_withhold_source_comment_names_file_reason_and_action(publication_repo: str):
    result = _withhold_case("include_parity")

    _job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    source_summary = gh.post_issue_comment.call_args.args[3]
    assert "`ydb/docs/en/a.md`" in source_summary
    assert "include_parity:" in source_summary
    assert "Действие:" in source_summary
