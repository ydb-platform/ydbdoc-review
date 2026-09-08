"""Tests for doc_translate / doc_verify workflow."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.errors import GitHubAPIError, GitHubConfigError
from ydbdoc_review.github.git_ops import (
    RefMutationOperation,
    RefMutationReceipt,
    RefMutationStatus,
    RemoteRefLease,
)
from ydbdoc_review.github.pr import PullRequestContext
from ydbdoc_review.github.provenance import (
    RuAuthority,
    TranslationArtifactProvenance,
    render_authority_evidence,
)
from ydbdoc_review.github.workflow import (
    DocJobResult,
    _enforce_report_checkout_bytes,
    run_doc_continue,
    run_doc_translate,
    run_doc_verify,
)
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.job_state import (
    CONTINUABILITY_STORE_KEY,
    ContinuabilityState,
    dump_continuability_json,
    load_continuability,
    mark_continuable,
)
from ydbdoc_review.ops.lifecycle import OpsContext, finish_ops_job
from ydbdoc_review.ops.recorder import LlmTranscriptRecorder
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
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


def _head_sha(repo_path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", repo_path, "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _fixture_provenance(
    repo_path: str,
    *,
    source_pr: int,
    candidate_sha: str | None = None,
) -> TranslationArtifactProvenance:
    candidate = candidate_sha or _head_sha(repo_path)
    parent = subprocess.check_output(
        ["git", "-C", repo_path, "rev-parse", f"{candidate}^"],
        text=True,
    ).strip()
    return TranslationArtifactProvenance(
        authority=RuAuthority(
            source_repo="o/r",
            source_pr=source_pr,
            source_base_sha=parent,
            source_head_sha=parent,
            baseline_sha=parent,
            ru_sha=parent,
            mode=RuAuthorityMode.CURRENT,
        ),
        candidate_sha=candidate,
    )


def _fixture_provenance_body(repo_path: str, *, source_pr: int) -> str:
    return render_authority_evidence(
        _fixture_provenance(repo_path, source_pr=source_pr)
    )


def _bind_fixture_artifact(
    _repo_path: str,
    selection,
    candidate_sha: str,
) -> TranslationArtifactProvenance:
    return TranslationArtifactProvenance(selection.authority, candidate_sha)


def _commit_empty(repo_path: str, message: str = "artifact fixture") -> str:
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    return _head_sha(repo_path)


def _update_receipt(
    branch: str,
    expected_sha: str | None,
    requested_sha: str,
) -> RefMutationReceipt:
    status = (
        RefMutationStatus.NOOP
        if expected_sha == requested_sha
        else RefMutationStatus.CHANGED
    )
    return RefMutationReceipt(
        lease=RemoteRefLease(branch, expected_sha),
        operation=RefMutationOperation.UPDATE,
        requested_sha=requested_sha,
        status=status,
        porcelain_flag="=" if status is RefMutationStatus.NOOP else "*",
        stdout="",
        stderr="",
    )


def _delete_receipt(branch: str, expected_sha: str) -> RefMutationReceipt:
    return RefMutationReceipt(
        lease=RemoteRefLease(branch, expected_sha),
        operation=RefMutationOperation.DELETE,
        requested_sha=None,
        status=RefMutationStatus.CHANGED,
        porcelain_flag="-",
        stdout="",
        stderr="",
    )


def _wire_publication_state(
    client: MagicMock,
    push: MagicMock,
    *,
    branch: str,
    initial_sha: str | None,
    published_pull: dict | None = None,
) -> dict[str, str | None]:
    state: dict[str, str | None] = {"sha": initial_sha}
    client.get_branch_sha.side_effect = lambda *_args, **_kwargs: state["sha"]

    def _push(*_args, **kwargs):
        requested = kwargs["source_sha"]
        state["sha"] = requested
        if published_pull is not None:
            published_pull["head"]["sha"] = requested
        return _update_receipt(branch, kwargs["expected_remote_sha"], requested)

    push.side_effect = _push
    return state


def _wire_translation_publication(
    client: MagicMock,
    push: MagicMock,
    source_pull: dict,
    *,
    source_number: int = 7,
) -> None:
    published_pull = {
        "title": f"Auto-translate docs from PR #{source_number}",
        "body": "",
        "draft": False,
        "head": {
            "ref": f"ydbdoc-review/pr-{source_number}",
            "sha": None,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": source_pull["base"]["ref"]},
    }
    client.get_pull.side_effect = lambda _owner, _repo, number: (
        source_pull if number == source_number else published_pull
    )
    client.update_pull_body.side_effect = (
        lambda _owner, _repo, _number, body: published_pull.__setitem__("body", body)
    )
    _wire_publication_state(
        client,
        push,
        branch=f"ydbdoc-review/pr-{source_number}",
        initial_sha=None,
        published_pull=published_pull,
    )


def _wire_verify_publication(
    client: MagicMock,
    push: MagicMock,
    source_pull: dict,
    *,
    source_number: int,
    branch: str,
    initial_sha: str | None = None,
) -> tuple[dict, dict[str, str | None]]:
    published_pull = {
        "title": f"Critic fixes for #{source_number}",
        "body": "",
        "draft": False,
        "head": {
            "ref": branch,
            "sha": initial_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": source_pull["base"]["ref"]},
    }
    client.get_pull.side_effect = lambda _owner, _repo, number: (
        source_pull if number == source_number else published_pull
    )
    state = _wire_publication_state(
        client,
        push,
        branch=branch,
        initial_sha=initial_sha,
        published_pull=published_pull,
    )
    return published_pull, state


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


@pytest.fixture(autouse=True)
def _isolate_workflow_llm_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workflow unit tests must not start real gRPC backend/client threads."""
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.begin_ops_job",
        lambda **_kwargs: (None, GateResult(ok=True), None),
    )
    client = MagicMock()
    client.usage_tracker = None
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.create_llm_client",
        lambda _config: client,
    )


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
        "ydbdoc_review.github.workflow.read_text_at_commit",
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


@pytest.mark.parametrize("verify_blocked", [False, True])
def test_run_doc_continue_updates_admission_after_verify_outcome(
    git_repo: str,
    verify_blocked: bool,
):
    pull = {
        "title": "Critic fixup",
        "head": {
            "ref": "ydbdoc-review/verify-40385",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "abc", "head": "abc"},
        translation_pr=50840,
    )
    store = InMemoryTranscriptStore()
    child_ctx = SimpleNamespace(
        store=store,
        run_id="child",
        parent_run_id=None,
        mode="continue",
        repo="o/r",
        source_pr=40385,
    )
    verified = DocJobResult(mode="doc_continue", pr_number=50840)
    if verify_blocked:
        verified.pr_result.completeness_gaps = ["still incomplete"]
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(child_ctx, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.run_doc_translate") as translate,
        patch(
            "ydbdoc_review.github.workflow.run_doc_verify",
            return_value=verified,
        ) as verify,
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_continue(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=50840,
            merge_base_with="HEAD",
            dry_run=False,
            config=load_config(env=_env()),
            instruction="fix anchors",
        )

    assert result is verified
    state = load_continuability(git_repo, 40385)
    assert state is not None
    assert state.allows_continue() is verify_blocked
    stored = store.get("child", CONTINUABILITY_STORE_KEY)
    assert stored is not None
    expected_flag = b'"continuable": true' if verify_blocked else b'"continuable": false'
    assert expected_flag in stored
    verify.assert_called_once()
    translate.assert_not_called()


@pytest.mark.parametrize(
    ("inline_commits", "terminal_blocked"),
    [(1, False), (3, True)],
    ids=["ordinary-recursion", "depth-limit-read-only-recursion"],
)
def test_run_doc_continue_finishes_one_job_after_recursive_inline_verify(
    git_repo: str,
    inline_commits: int,
    terminal_blocked: bool,
):
    """A recursive verify must finish the dispatcher-admitted lifecycle once."""
    _wire_en_toc_for_a(git_repo)
    initial_sha = _head_sha(git_repo)
    fixup_pull = {
        "title": "Critic fixes for #40385",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/verify-40385",
            "sha": initial_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": initial_sha},
    }
    source_pull = {
        "title": "docs: source",
        "body": "",
        "head": {
            "ref": "feature/docs",
            "sha": initial_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": initial_sha},
    }
    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": initial_sha, "head": initial_sha},
        translation_pr=50840,
    )
    store = InMemoryTranscriptStore()
    ledger = InMemoryRunsLedger()
    ops_ctx = OpsContext(
        actor="tester",
        store=store,
        ledger=ledger,
        run_id="continue-run",
        run_day="2026-09-07",
        parent_run_id="parent-run",
        mode="continue",
        repo="o/r",
        source_pr=40385,
        translation_pr=50840,
        continue_index=1,
        continue_feedback="apply critic repair",
        recorder=LlmTranscriptRecorder(),
        budget_rub=5000.0,
    )
    client = MagicMock(usage_tracker=UsageTracker())
    verify_attempt = 0

    def verify_result(*_args, **_kwargs) -> PRTranslationResult:
        nonlocal verify_attempt
        verify_attempt += 1
        result = _fake_pr_result()
        if verify_attempt == inline_commits + 1 and terminal_blocked:
            result.completeness_gaps = ["terminal blocker"]
        return result

    commit_attempt = 0

    def commit_fix(*_args, **_kwargs) -> bool:
        nonlocal commit_attempt
        if commit_attempt >= inline_commits:
            return False
        commit_attempt += 1
        _commit_empty(git_repo, f"inline verify {commit_attempt}")
        return True

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ) as begin,
        patch(
            "ydbdoc_review.github.workflow.finish_ops_job",
            wraps=finish_ops_job,
        ) as finish,
        patch("ydbdoc_review.github.workflow.create_llm_client", return_value=client),
        patch(
            "ydbdoc_review.github.workflow._run_verify_pairs",
            side_effect=verify_result,
        ) as run_pairs,
        patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"),
        patch(
            "ydbdoc_review.github.workflow.git_commit_paths",
            side_effect=commit_fix,
        ),
        patch("ydbdoc_review.github.workflow.push_branch") as push,
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/en/a.md", "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=lambda _gh, _owner, _repo, number: (
                [("ydb/docs/ru/a.md", "modified")]
                if number == 40385
                else [("ydb/docs/en/a.md", "modified")]
            ),
        ),
    ):
        mock_gh.return_value.get_pull.side_effect = (
            lambda _owner, _repo, number: source_pull if number == 40385 else fixup_pull
        )
        _wire_publication_state(
            mock_gh.return_value,
            push,
            branch="ydbdoc-review/verify-40385",
            initial_sha=initial_sha,
            published_pull=fixup_pull,
        )
        mock_gh.return_value.iter_issue_comments.return_value = iter([])
        mock_gh.return_value.post_issue_comment.return_value = "url"
        job = run_doc_continue(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=50840,
            merge_base_with="HEAD",
            dry_run=False,
            config=load_config(env=_env()),
            instruction="apply critic repair",
        )

    assert run_pairs.call_count == inline_commits + 1
    assert push.call_count == inline_commits
    begin.assert_called_once()
    finish.assert_called_once()
    assert finish.call_args.args[0] is ops_ctx
    assert len(ledger.records) == 1
    assert ledger.records[0].run_id == "continue-run"
    assert ledger.records[0].status == "ok"
    manifest = store.get("continue-run", "manifest.json")
    assert manifest is not None
    assert b'"status": "ok"' in manifest
    terminal_state = load_continuability(git_repo, 40385)
    assert terminal_state is not None
    assert terminal_state.allows_continue() is terminal_blocked
    assert ("terminal blocker" in job.pr_result.completeness_gaps) is terminal_blocked


def test_direct_gate_skipping_verify_cannot_inject_continue_context(git_repo: str):
    """Only an internal recursive verify may carry a gate-skipping ops context."""
    initial_sha = _head_sha(git_repo)
    pull = {
        "title": "Critic fixes for #40385",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/verify-40385",
            "sha": initial_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": initial_sha},
    }
    ops_ctx = SimpleNamespace(
        store=InMemoryTranscriptStore(),
        run_id="continue-run",
        parent_run_id="parent-run",
        mode="continue",
        repo="o/r",
        source_pr=40385,
    )
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        mock_gh.return_value.get_pull.return_value = pull
        with pytest.raises(
            RuntimeError,
            match="continue ops admission cannot be skipped by an external verify",
        ):
            run_doc_verify(
                repo_path=git_repo,
                github_repo="o/r",
                pr_number=50840,
                merge_base_with="HEAD",
                config=load_config(env=_env()),
                skip_ops_gates=True,
                ops_mode="continue",
                _ops_ctx=ops_ctx,
            )


def test_external_verify_cannot_forge_recursive_continue_admission(git_repo: str):
    """Public recursion-shaped arguments cannot authorize gate skipping."""
    initial_sha = _head_sha(git_repo)
    supplied_context = PullRequestContext(
        owner="o",
        repo="r",
        number=50840,
        title="Critic fixes for #40385",
        head_ref="ydbdoc-review/verify-40385",
        head_sha=initial_sha,
        head_repo_full_name="o/r",
        head_repo_https_url="https://github.com/o/r.git",
        base_ref="main",
        base_sha=initial_sha,
    )
    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": initial_sha, "head": initial_sha},
        translation_pr=50840,
    )
    ops_ctx = SimpleNamespace(
        store=InMemoryTranscriptStore(),
        run_id="continue-run",
        parent_run_id="parent-run",
        mode="continue",
        repo="o/r",
        source_pr=40385,
    )
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient"),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            side_effect=AssertionError("external call opened a second job"),
        ),
        patch(
            "ydbdoc_review.github.workflow._snapshot_destination_lease",
            side_effect=AssertionError("forged call reached verify work"),
        ),
    ):
        with pytest.raises(
            RuntimeError,
            match="continue ops admission cannot be skipped by an external verify",
        ):
            run_doc_verify(
                repo_path=git_repo,
                github_repo="o/r",
                pr_number=50840,
                merge_base_with="HEAD",
                config=load_config(env=_env()),
                skip_ops_gates=True,
                ops_mode="continue",
                _fixup_rerun_depth=1,
                _inline_fixup_context=supplied_context,
                _ops_ctx=ops_ctx,
            )


def test_direct_continue_verify_cannot_skip_ops_admission(git_repo: str):
    """Saved state alone cannot turn an external gate-skipping call internal."""
    initial_sha = _head_sha(git_repo)
    pull = {
        "title": "Critic fixes for #40385",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/verify-40385",
            "sha": initial_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": initial_sha},
    }
    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": initial_sha, "head": initial_sha},
        translation_pr=50840,
    )
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow._snapshot_destination_lease",
            side_effect=AssertionError("gate-skipping call reached verify work"),
        ),
    ):
        mock_gh.return_value.get_pull.return_value = pull
        with pytest.raises(
            RuntimeError,
            match="continue ops admission cannot be skipped by an external verify",
        ):
            run_doc_verify(
                repo_path=git_repo,
                github_repo="o/r",
                pr_number=50840,
                merge_base_with="HEAD",
                config=load_config(env=_env()),
                skip_ops_gates=True,
                ops_mode="continue",
            )


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


def test_run_doc_continue_uses_parent_store_admission_in_fresh_checkout(
    git_repo: str,
    tmp_path: Path,
):
    pull = {
        "title": "Auto-translate docs from PR #40385",
        "head": {
            "ref": "ydbdoc-review/pr-40385",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    store = InMemoryTranscriptStore()
    parent_ctx = SimpleNamespace(store=store, run_id="parent")
    from ydbdoc_review.github import workflow as workflow_module

    workflow_module._persist_continuability(
        str(tmp_path / "old-checkout"),
        source_pr=40385,
        fixed_shas={"merge_base": "abc", "head": "abc"},
        translation_pr=50840,
        unfinished=True,
        ops_ctx=parent_ctx,
    )
    assert load_continuability(git_repo, 40385) is None

    child_ctx = SimpleNamespace(
        store=store,
        run_id="child",
        parent_run_id="parent",
        mode="continue",
        repo="o/r",
        source_pr=40385,
    )
    translated = DocJobResult(mode="doc_continue", pr_number=40385)
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(child_ctx, GateResult(ok=True), None),
        ) as begin,
        patch(
            "ydbdoc_review.github.workflow.run_doc_translate",
            return_value=translated,
        ) as translate,
        patch("ydbdoc_review.github.workflow.run_doc_verify") as verify,
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_continue(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=50840,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            instruction="fix anchors",
        )

    assert result is translated
    begin.assert_called_once()
    assert translate.call_args.kwargs["parent_run_id"] == "parent"
    assert translate.call_args.kwargs["_ops_ctx"] is child_ctx
    verify.assert_not_called()


@pytest.mark.parametrize("local_state", ["denied", "wrong_translation_pr"])
def test_run_doc_continue_rejects_explicit_ineligible_local_state(
    git_repo: str,
    local_state: str,
):
    pull = {
        "title": "Auto-translate docs from PR #40385",
        "head": {
            "ref": "ydbdoc-review/pr-40385",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    store = InMemoryTranscriptStore()
    store.put(
        "parent",
        CONTINUABILITY_STORE_KEY,
        dump_continuability_json(
            ContinuabilityState(
                continuable=True,
                unfinished_stage="verify",
                fixed_shas={"merge_base": "abc", "head": "abc"},
                source_pr=40385,
                translation_pr=50840,
            )
        ),
    )
    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "abc", "head": "abc"},
        translation_pr=999 if local_state == "wrong_translation_pr" else 50840,
    )
    if local_state == "denied":
        from ydbdoc_review.ops.job_state import clear_continuability

        clear_continuability(git_repo, 40385)
    child_ctx = SimpleNamespace(
        store=store,
        run_id="child",
        parent_run_id="parent",
        mode="continue",
        repo="o/r",
        source_pr=40385,
    )
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(child_ctx, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.run_doc_translate") as translate,
        patch("ydbdoc_review.github.workflow.run_doc_verify") as verify,
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_continue(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=50840,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            instruction="fix anchors",
        )

    assert result.blocked is True
    translate.assert_not_called()
    verify.assert_not_called()


def test_run_doc_continue_preserves_acl_denial_before_saved_admission(git_repo: str):
    mark_continuable(
        git_repo,
        source_pr=40385,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "abc", "head": "abc"},
        translation_pr=50840,
    )
    pull = {
        "title": "Auto-translate docs from PR #40385",
        "head": {
            "ref": "ydbdoc-review/pr-40385",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(
                None,
                GateResult(ok=False, reason="denied", status="denied_acl"),
                "denied",
            ),
        ),
        patch("ydbdoc_review.github.workflow.run_doc_translate") as translate,
        patch("ydbdoc_review.github.workflow.run_doc_verify") as verify,
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_continue(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=50840,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            instruction="fix anchors",
        )

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
    checkout_sha = _head_sha(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": checkout_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": checkout_sha},
    }

    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
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


def test_run_doc_translate_en_toc_graph_keeps_b_miss_separate_from_r(
    git_repo: str,
) -> None:
    root = Path(git_repo)
    ru_path = "ydb/docs/ru/a.md"
    missing_en_toc = "ydb/docs/en/core/missing/toc_p.yaml"
    present_en_page = "ydb/docs/en/core/present.md"
    (root / missing_en_toc).parent.mkdir(parents=True, exist_ok=True)
    (root / missing_en_toc).write_text("R-EN-TOC-SENTINEL\n", encoding="utf-8")
    (root / present_en_page).write_text("R-EN-PAGE-SENTINEL\n", encoding="utf-8")
    (root / ru_path).write_text("R-RU-SENTINEL\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "historical R"], cwd=git_repo, check=True)
    r_sha = _head_sha(git_repo)
    h0_sha = subprocess.run(
        ["git", "rev-parse", f"{r_sha}^"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    (root / missing_en_toc).unlink()
    (root / present_en_page).write_text("B-EN-PAGE-SENTINEL\n", encoding="utf-8")
    (root / ru_path).write_text("B-RU-SENTINEL\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "frozen B"], cwd=git_repo, check=True)
    b_sha = _head_sha(git_repo)

    (root / missing_en_toc).write_text("WORKTREE-EN-TOC-SENTINEL\n", encoding="utf-8")
    (root / present_en_page).write_text("WORKTREE-EN-PAGE-SENTINEL\n", encoding="utf-8")
    (root / ru_path).write_text("WORKTREE-RU-SENTINEL\n", encoding="utf-8")

    pull = {
        "title": "historical docs",
        "head": {
            "ref": "feature/docs",
            "sha": r_sha,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": h0_sha},
        "merged": True,
        "state": "closed",
        "merge_commit_sha": r_sha,
    }
    callback_observations: dict[str, str | None] = {}

    def inspect_graph_reader(
        _repo_path: str,
        *,
        read_text,
        **_kwargs,
    ) -> set[str]:
        callback_observations[missing_en_toc] = read_text(missing_en_toc)
        callback_observations[present_en_page] = read_text(present_en_page)
        callback_observations[ru_path] = read_text(ru_path)
        return set()

    config = load_config(
        env={
            **_env(),
            "YDBDOC_TRANSLATION_RU_AUTHORITY_MODE": "source-preserving",
        }
    )
    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
            mock_gh.return_value.get_pull.return_value = pull
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                return_value=[(ru_path, "modified")],
            ), patch(
                "ydbdoc_review.github.workflow.build_en_toc_reachable_from_repo",
                side_effect=inspect_graph_reader,
            ):
                result = run_doc_translate(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=7,
                    merge_base_with=b_sha,
                    dry_run=True,
                    config=config,
                )

    assert result.dry_run
    assert callback_observations == {
        missing_en_toc: None,
        present_en_page: "B-EN-PAGE-SENTINEL\n",
        ru_path: "R-RU-SENTINEL\n",
    }


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
    checkout_sha = _commit_empty(git_repo)

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": _fixture_provenance_body(git_repo, source_pr=3),
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": checkout_sha,
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
            mock_gh.return_value.get_branch_sha.return_value = checkout_sha
            mock_gh.return_value.get_file_text.return_value = "RU.\n"
            mock_gh.return_value.iter_issue_comments.return_value = iter([])
            with patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=[("ydb/docs/en/a.md", "modified")],
            ), patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                side_effect=lambda _gh, _owner, _repo, number: (
                    [("ydb/docs/en/a.md", "modified")]
                    if number == 11
                    else [("ydb/docs/ru/a.md", "modified")]
                ),
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


def test_attestation_backed_verify_preserves_body_and_never_publishes_k2(
    git_repo: str,
) -> None:
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")
    root_c = _commit_empty(git_repo, "root C")
    candidate_k = _commit_empty(git_repo, "repaired K")
    provenance = replace(
        _fixture_provenance(git_repo, source_pr=3, candidate_sha=root_c),
        coverage_version=1,
        coverage_run_id="old-run",
        coverage_digest="1" * 64,
    )
    original_body = (
        "RED: old human-readable caution.\n\n"
        + render_authority_evidence(provenance)
    )
    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": original_body,
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": candidate_k,
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

    proposed = _fake_pr_result()
    proposed.pair_results[0].target_text = "Critic-proposed K2.\n"
    assert proposed.pair_results[0].file_result is not None
    proposed.pair_results[0].file_result.final_text = "Critic-proposed K2.\n"
    effective_evidence = SimpleNamespace(plans=())
    store = InMemoryTranscriptStore()
    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs", return_value=proposed
    ), patch(
        "ydbdoc_review.github.workflow.load_attested_coverage_evidence",
        return_value=effective_evidence,
    ) as load_attested, patch(
        "ydbdoc_review.github.workflow.validate_coverage_evidence"
    ), patch(
        "ydbdoc_review.github.workflow.push_branch"
    ) as push, patch(
        "ydbdoc_review.github.workflow.GitHubClient"
    ) as gh_cls:
        gh = gh_cls.return_value
        gh.get_pull.side_effect = _get_pull
        gh.get_branch_sha.return_value = candidate_k
        gh.get_file_text.return_value = "RU.\n"
        gh.iter_issue_comments.return_value = iter([])
        with patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/en/a.md", "modified")],
        ), patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=lambda _gh, _owner, _repo, number: (
                [("ydb/docs/en/a.md", "modified")]
                if number == 11
                else [("ydb/docs/ru/a.md", "modified")]
            ),
        ):
            result = run_doc_verify(
                repo_path=git_repo,
                github_repo="o/r",
                pr_number=11,
                merge_base_with="HEAD",
                config=load_config(env=_env()),
                _coverage_store=store,
            )

    load_attested.assert_called_once()
    gh.update_pull_body.assert_not_called()
    push.assert_not_called()
    assert pull["body"] == original_body
    assert result.pushed is False
    assert result.committed is False
    assert result.pr_result.pair_results[0].file_result is not None
    assert "report_checkout_mismatch" in " ".join(
        result.pr_result.pair_results[0].file_result.heuristic_blocking
    )


def test_run_doc_translate_no_pairs(git_repo: str):
    checkout_sha = _head_sha(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": checkout_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": checkout_sha},
    }
    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
        mock_gh.return_value.get_pull.return_value = pull
        with patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("README.md", "modified")],
        ), patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[("README.md", "modified")],
        ):
            result = run_doc_translate(
                repo_path=git_repo,
                github_repo="o/r",
                pr_number=7,
                merge_base_with="HEAD",
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
    checkout_sha = _head_sha(git_repo)
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
            "sha": checkout_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": checkout_sha},
    }

    with patch("ydbdoc_review.github.workflow.GitHubClient") as gh_cls, patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/ru/core/toc_p.yaml", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        return_value=[("ydb/docs/ru/core/toc_p.yaml", "modified")],
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

    checkout_sha = _head_sha(git_repo)
    pull = {
        "title": "Fix glossary links",
        "head": {
            "ref": "docs-glossary",
            "sha": checkout_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": checkout_sha},
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
                            merge_base_with="HEAD",
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
    checkout_sha = _head_sha(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": checkout_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": checkout_sha},
    }

    def verify_after_saved_admission(**_kwargs) -> DocJobResult:
        state = load_continuability(git_repo, 7)
        assert state is not None
        assert state.allows_continue()
        assert state.unfinished_stage == "verify"
        assert state.translation_pr == 99
        return _mock_inline_verify_job()

    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"):
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch(
                        "ydbdoc_review.github.workflow.run_doc_verify",
                        side_effect=verify_after_saved_admission,
                    ) as mock_verify:
                        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                            _wire_translation_publication(
                                mock_gh.return_value,
                                push,
                                pull,
                            )
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
                            ), patch(
                                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                                return_value=[("ydb/docs/ru/a.md", "modified")],
                            ), patch(
                                "ydbdoc_review.github.workflow.bind_translation_artifact",
                                side_effect=_bind_fixture_artifact,
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
    terminal_state = load_continuability(git_repo, 7)
    assert terminal_state is not None
    assert not terminal_state.allows_continue()
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
    checkout_sha = _head_sha(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": checkout_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": checkout_sha},
    }
    with patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=_fake_pr_result()):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"):
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch(
                        "ydbdoc_review.github.workflow.run_doc_verify",
                        return_value=_mock_inline_verify_job(),
                    ):
                        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                            _wire_translation_publication(
                                mock_gh.return_value,
                                push,
                                pull,
                            )
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
                            ), patch(
                                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                                return_value=[("ydb/docs/ru/a.md", "modified")],
                            ), patch(
                                "ydbdoc_review.github.workflow.bind_translation_artifact",
                                side_effect=_bind_fixture_artifact,
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
    checkout_sha = _head_sha(git_repo)
    pull = {
        "title": "docs",
        "head": {
            "ref": "parameterized-query",
            "sha": checkout_sha,
            "repo": {
                "clone_url": "https://github.com/contrib/ydb.git",
                "full_name": "contrib/ydb",
            },
        },
        "base": {"ref": "main", "sha": checkout_sha},
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
                            _wire_translation_publication(
                                mock_gh.return_value,
                                push,
                                pull,
                            )
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
                            ), patch(
                                "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                                return_value=[("ydb/docs/ru/a.md", "modified")],
                            ), patch(
                                "ydbdoc_review.github.workflow.bind_translation_artifact",
                                side_effect=_bind_fixture_artifact,
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
                        _wire_verify_publication(
                            mock_gh.return_value,
                            push,
                            pull,
                            source_number=11,
                            branch="ydbdoc-review/verify-11",
                        )
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
    mock_gh.return_value.delete_branch.assert_not_called()
    mock_gh.return_value.create_pull.assert_called_once()
    create_kwargs = mock_gh.return_value.create_pull.call_args.kwargs
    assert create_kwargs["head"] == "ydbdoc-review/verify-11"
    assert create_kwargs["base"] == "main"
    assert result.translation_pr_number == 99
    assert result.source_comment_url == "url"
    posted_bodies = [c.args[3] for c in mock_gh.return_value.post_issue_comment.call_args_list]
    assert any("#99" in body for body in posted_bodies)


def test_run_doc_verify_fork_head_resets_existing_fixup_branch(git_repo: str):
    """Second run replaces a stale fixup branch with leased delete/create."""
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
    candidate_sha = _head_sha(git_repo)
    stale_sha = subprocess.check_output(
        [
            "git",
            "-C",
            git_repo,
            "commit-tree",
            f"{candidate_sha}^{{tree}}",
            "-p",
            candidate_sha,
            "-m",
            "stale fixup fixture",
        ],
        text=True,
    ).strip()

    with patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        return_value=_fake_pr_result(),
    ):
        with patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"):
            with patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True):
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch(
                        "ydbdoc_review.github.workflow.delete_remote_branch_with_lease"
                    ) as delete:
                        with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                            _published, state = _wire_verify_publication(
                                mock_gh.return_value,
                                push,
                                pull,
                                source_number=11,
                                branch="ydbdoc-review/verify-11",
                                initial_sha=stale_sha,
                            )

                            def _delete(*_args, **kwargs):
                                state["sha"] = None
                                return _delete_receipt(
                                    "ydbdoc-review/verify-11",
                                    kwargs["expected_remote_sha"],
                                )

                            delete.side_effect = _delete
                            mock_gh.return_value.iter_issue_comments.return_value = iter([])
                            mock_gh.return_value.post_issue_comment.return_value = "url"
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

    delete.assert_called_once()
    assert delete.call_args.kwargs["expected_remote_sha"] == stale_sha
    mock_gh.return_value.delete_branch.assert_not_called()
    push.assert_called_once()
    assert push.call_args.args[2] == "ydbdoc-review/verify-11"
    assert result.translation_pr_number == 100


def test_run_doc_verify_does_not_delete_fixup_branch_without_publication(
    git_repo: str,
):
    """Read-only verify leaves a pre-existing fixup branch untouched."""
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

    mock_gh.return_value.delete_branch.assert_not_called()


def test_run_doc_verify_translation_pr_pushes_fixes_inline(git_repo: str):
    """Translation PR: critic fixes commit on ydbdoc-review/pr-N, no fixup PR (§6.75)."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")
    checkout_sha = _commit_empty(git_repo)

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": _fixture_provenance_body(git_repo, source_pr=3),
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": checkout_sha,
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
                        _wire_publication_state(
                            mock_gh.return_value,
                            push,
                            branch="ydbdoc-review/pr-3",
                            initial_sha=checkout_sha,
                            published_pull=pull,
                        )
                        mock_gh.return_value.iter_issue_comments.return_value = iter([])
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/a.md", "modified")],
                        ), patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                            side_effect=lambda _gh, _owner, _repo, number: (
                                [("ydb/docs/en/a.md", "modified")]
                                if number == 11
                                else [("ydb/docs/ru/a.md", "modified")]
                            ),
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
                        _wire_verify_publication(
                            mock_gh.return_value,
                            push,
                            pull,
                            source_number=7,
                            branch="ydbdoc-review/verify-7",
                        )
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
    content_sha = _commit_empty(git_repo)

    pull = {
        "title": "Auto-translate docs from PR #3",
        "body": _fixture_provenance_body(git_repo, source_pr=3),
        "head": {
            "ref": "ydbdoc-review/pr-3",
            "sha": content_sha,
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
                with patch("ydbdoc_review.github.workflow.push_branch") as push:
                    with patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh:
                        mock_gh.return_value.get_pull.return_value = pull
                        _wire_publication_state(
                            mock_gh.return_value,
                            push,
                            branch="ydbdoc-review/pr-3",
                            initial_sha=content_sha,
                            published_pull=pull,
                        )
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
                        ), patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                            side_effect=lambda _gh, _owner, _repo, number: (
                                [("ydb/docs/en/a.md", "modified")]
                                if number == 11
                                else [("ydb/docs/ru/a.md", "modified")]
                            ),
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
    checkout_sha = _commit_empty(git_repo)

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
        "body": _fixture_provenance_body(git_repo, source_pr=45667),
        "head": {
            "ref": "ydbdoc-review/pr-45667",
            "sha": checkout_sha,
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
                        mock_gh.return_value.get_branch_sha.return_value = checkout_sha
                        mock_gh.return_value.get_file_text.return_value = "RU.\n"
                        mock_gh.return_value.iter_issue_comments.return_value = iter([])
                        mock_gh.return_value.post_issue_comment.return_value = "url"
                        with patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                            return_value=[("ydb/docs/en/core/concepts/glossary.md", "modified")],
                        ), patch(
                            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
                            side_effect=lambda _gh, _owner, _repo, number: (
                                [("ydb/docs/en/core/concepts/glossary.md", "modified")]
                                if number == 49578
                                else [("ydb/docs/ru/core/concepts/glossary.md", "modified")]
                            ),
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
