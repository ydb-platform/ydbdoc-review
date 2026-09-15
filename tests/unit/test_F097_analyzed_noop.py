"""F-097: Analyze-proven no-op exits at the workflow boundary."""

from __future__ import annotations

import json
import subprocess
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import (
    run_doc_continue,
    run_doc_translate,
)
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.job_state import mark_continuable
from ydbdoc_review.ops.lifecycle import OpsContext
from ydbdoc_review.ops.recorder import LlmTranscriptRecorder
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.translation_preflight import PreflightResult
from ydbdoc_review.pipeline.types import PRTranslationResult

SOURCE_PR = 7
CONTINUE_PR = 99
RU_PATH = "ydb/docs/ru/a.md"
EN_PATH = "ydb/docs/en/a.md"
ALIGNMENT_REASON = "Formatting changed; the RU and EN meaning is already aligned."


def _config():
    return load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )


def _analyze_response(*, aligned: bool = True) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "ru_path": RU_PATH,
                    "en_path": EN_PATH,
                    "ru_present": aligned,
                    "en_present": True,
                    "semantically_aligned": aligned,
                    "needs_generation_for": None,
                    "summary": ALIGNMENT_REASON if aligned else "RU source is absent",
                }
            ]
        }
    )


def _client(response: str) -> YandexLLMClient:
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
        usage=SimpleNamespace(prompt_tokens=17, completion_tokens=9),
    )
    openai = MagicMock()
    openai.chat.completions.create.return_value = completion
    cfg = _config()
    return YandexLLMClient(
        folder_id="folder",
        api_key="key",
        llm=cfg.llm,
        client=openai,
    )


def _repo(tmp_path: Path) -> tuple[str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / RU_PATH).parent.mkdir(parents=True)
    (repo / EN_PATH).parent.mkdir(parents=True)
    (repo / RU_PATH).write_text("Привет.\n", encoding="utf-8")
    (repo / EN_PATH).write_text("Hello.\n", encoding="utf-8")
    (repo / "ydb/docs/en/toc_p.yaml").write_text(
        "items:\n- name: A\n  href: a.md\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True
    )
    base_sha = _git(repo, "rev-parse", "HEAD")
    (repo / RU_PATH).write_text("Привет.  \n", encoding="utf-8")
    subprocess.run(["git", "add", RU_PATH], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "format source"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return str(repo), base_sha, _git(repo, "rev-parse", "HEAD")


def _git(repo: Path | str, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _source_pull(base_sha: str, head_sha: str) -> dict[str, object]:
    return {
        "title": "docs: formatting only",
        "body": "",
        "state": "open",
        "merged": False,
        "head": {
            "ref": "feature/docs",
            "sha": head_sha,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": base_sha},
    }


def _continue_pull(head_sha: str) -> dict[str, object]:
    return {
        "title": f"Auto-translate docs from PR #{SOURCE_PR}",
        "body": "",
        "user": {"login": "github-actions[bot]"},
        "state": "open",
        "merged": False,
        "head": {
            "ref": f"ydbdoc-review/pr-{SOURCE_PR}",
            "sha": head_sha,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": head_sha},
    }


def _ops_context(*, mode: str, translation_pr: int | None = None) -> OpsContext:
    return OpsContext(
        actor="test-actor",
        run_id=f"f097-{mode}",
        run_day="2026-09-12",
        mode=mode,
        repo="o/r",
        source_pr=SOURCE_PR,
        ledger=InMemoryRunsLedger(),
        store=InMemoryTranscriptStore(),
        recorder=LlmTranscriptRecorder(),
        budget_rub=5000.0,
        parent_run_id="parent-run" if mode == "continue" else None,
        continue_index=1 if mode == "continue" else 0,
        translation_pr=translation_pr,
        continue_feedback="Keep the agreed terminology" if mode == "continue" else None,
    )


def _workflow_patches(
    stack: ExitStack,
    *,
    github: MagicMock,
    client: YandexLLMClient,
    api_changes: list[tuple[str, str]] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    stack.enter_context(
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=github)
    )
    stack.enter_context(
        patch("ydbdoc_review.github.workflow.create_llm_client", return_value=client)
    )
    stack.enter_context(
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=api_changes or [(RU_PATH, "modified")],
        )
    )
    stack.enter_context(
        patch(
            "ydbdoc_review.github.workflow.preflight_translation",
            return_value=PreflightResult(blockers=(), deferred_checks=()),
        )
    )
    translator = MagicMock(name="translator")
    heavy_qa = MagicMock(name="heavy_qa")

    def heavy_pipeline(*_args, **_kwargs) -> PRTranslationResult:
        translator()
        heavy_qa()
        return PRTranslationResult()

    stack.enter_context(
        patch("ydbdoc_review.github.workflow.run_pr_translation", side_effect=heavy_pipeline)
    )
    prepare = stack.enter_context(
        patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base")
    )
    commit = stack.enter_context(
        patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True)
    )
    push = stack.enter_context(patch("ydbdoc_review.github.workflow.push_branch"))
    return translator, heavy_qa, prepare, commit, push


def test_F097_new_translation_ignores_analyze_alignment(tmp_path: Path) -> None:
    """D-002: a new run performs translation instead of an Analyze-proven no-op."""
    repo, base_sha, head_sha = _repo(tmp_path)
    client = _client(_analyze_response())
    ops = _ops_context(mode="translate")
    github = MagicMock()
    github.get_pull.return_value = _source_pull(base_sha, head_sha)
    github.get_branch_sha.return_value = None
    github.post_issue_comment.return_value = "source-comment"
    before = (head_sha, _git(repo, "branch", "--format=%(refname:short)"))

    with ExitStack() as stack:
        translator, heavy_qa, _prepare, _commit, push = _workflow_patches(
            stack, github=github, client=client
        )
        job = run_doc_translate(
            repo_path=repo,
            github_repo="o/r",
            pr_number=SOURCE_PR,
            merge_base_with=base_sha,
            config=_config(),
            _ops_ctx=ops,
        )

    assert not job.analyzed_noop
    assert [record.role for record in client.usage_tracker.records] == []
    translator.assert_called_once()
    heavy_qa.assert_called_once()
    # The mocked translator returns no bytes: this is not a successful no-op.
    assert job.translation_pr_number is None
    github.create_pull.assert_not_called()
    push.assert_not_called()
    assert before == (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "branch", "--format=%(refname:short)"),
    )


def test_F097_continue_instruction_reenters_translation_scope(tmp_path: Path) -> None:
    """Continue feedback invalidates no-op and preserves an incomplete result."""
    repo, base_sha, head_sha = _repo(tmp_path)
    client = _client(_analyze_response())
    ops = _ops_context(mode="continue", translation_pr=CONTINUE_PR)
    mark_continuable(
        repo,
        source_pr=SOURCE_PR,
        unfinished_stage="verify",
        fixed_shas={"merge_base": base_sha, "head": head_sha},
        translation_pr=CONTINUE_PR,
    )
    github = MagicMock()
    github.get_pull.side_effect = lambda _owner, _repo, number: (
        _source_pull(base_sha, head_sha)
        if number == SOURCE_PR
        else _continue_pull(head_sha)
    )
    github.get_branch_sha.return_value = None
    github.post_issue_comment.side_effect = lambda _o, _r, number, _body: (
        f"comment-{number}"
    )

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.begin_ops_job",
                return_value=(ops, GateResult(ok=True), None),
            )
        )
        translator, heavy_qa, prepare, commit, push = _workflow_patches(
            stack, github=github, client=client
        )
        job = run_doc_continue(
            repo_path=repo,
            github_repo="o/r",
            pr_number=CONTINUE_PR,
            merge_base_with=base_sha,
            config=_config(),
            instruction="Keep the agreed terminology",
        )

    # F-142: a continue instruction invalidates the stale Analyze no-op and
    # re-enters the ordinary translation path.
    translator.assert_called_once()
    heavy_qa.assert_called_once()
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    assert job.translation_pr_number is None
    assert job.source_comment_url == f"comment-{SOURCE_PR}"
    assert job.translation_comment_url is None
    comments = github.post_issue_comment.call_args_list
    assert [call.args[2] for call in comments] == [SOURCE_PR]
    for call in comments:
        body = call.args[3]
        assert "перевод не требуется" not in body
        assert "ydb/docs/en/a.md" in body

    # F-142 skips a stale Analyze call entirely when continue feedback exists.
    assert ops.store.get(ops.run_id, "llm/001-analyze-req.json") is None


def test_F097_unsupported_pair_cannot_publish_aligned_noop(tmp_path: Path) -> None:
    """Missing supported content falls through to translation, never aligned no-op."""
    repo, base_sha, head_sha = _repo(tmp_path)
    client = _client(_analyze_response(aligned=False))
    ops = _ops_context(mode="translate")
    github = MagicMock()
    github.get_pull.return_value = _source_pull(base_sha, head_sha)
    github.get_branch_sha.return_value = None

    with ExitStack() as stack:
        translator, heavy_qa, _prepare, _commit, _push = _workflow_patches(
            stack, github=github, client=client
        )
        run_doc_translate(
            repo_path=repo,
            github_repo="o/r",
            pr_number=SOURCE_PR,
            merge_base_with=base_sha,
            no_commit=True,
            config=_config(),
            _ops_ctx=ops,
        )

    translator.assert_called_once()
    heavy_qa.assert_called_once()
    github.post_issue_comment.assert_not_called()


def test_F097_complete_evidence_cannot_skip_new_translation(tmp_path: Path) -> None:
    """D-002: a new translation bypasses Analyze even with complete old evidence."""
    repo, base_sha, _head_sha = _repo(tmp_path)
    long_prefix = "X" * 8_100
    Path(repo, RU_PATH).write_text(f"{long_prefix} RU differs\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", RU_PATH], check=True)
    subprocess.run(
        ["git", "-C", repo, "commit", "-m", "long source"],
        check=True,
        capture_output=True,
    )
    head_sha = _git(repo, "rev-parse", "HEAD")
    client = _client(_analyze_response())
    ops = _ops_context(mode="translate")
    github = MagicMock()
    github.get_pull.return_value = _source_pull(base_sha, head_sha)
    github.get_branch_sha.return_value = None

    with ExitStack() as stack:
        translator, heavy_qa, _prepare, _commit, _push = _workflow_patches(
            stack, github=github, client=client
        )
        run_doc_translate(
            repo_path=repo,
            github_repo="o/r",
            pr_number=SOURCE_PR,
            merge_base_with=base_sha,
            no_commit=True,
            config=_config(),
            _ops_ctx=ops,
        )

    translator.assert_called_once()
    heavy_qa.assert_called_once()
    assert [record.role for record in client.usage_tracker.records] == []


def test_F097_mixed_source_range_cannot_publish_aligned_noop(tmp_path: Path) -> None:
    """Skipped non-Markdown source files make the Analyze no-op proof incomplete."""
    repo, base_sha, head_sha = _repo(tmp_path)
    client = _client(_analyze_response())
    ops = _ops_context(mode="translate")
    github = MagicMock()
    github.get_pull.return_value = _source_pull(base_sha, head_sha)
    github.get_branch_sha.return_value = None

    with ExitStack() as stack:
        translator, heavy_qa, _prepare, _commit, _push = _workflow_patches(
            stack,
            github=github,
            client=client,
            api_changes=[(RU_PATH, "modified"), ("public/materials/logo.svg", "modified")],
        )
        run_doc_translate(
            repo_path=repo,
            github_repo="o/r",
            pr_number=SOURCE_PR,
            merge_base_with=base_sha,
            no_commit=True,
            config=_config(),
            _ops_ctx=ops,
        )

    translator.assert_called_once()
    heavy_qa.assert_called_once()
    assert client.usage_tracker.records == []


def test_F097_model_cannot_claim_missing_pair_is_aligned(tmp_path: Path) -> None:
    """Contradictory Analyze claims cannot conceal an absent translation body."""
    repo, base_sha, head_sha = _repo(tmp_path)
    client = _client(_analyze_response())
    ops = _ops_context(mode="translate")
    github = MagicMock()
    github.get_pull.return_value = _source_pull(base_sha, head_sha)
    github.get_branch_sha.return_value = None
    missing_en = PairContent(
        pair=DocPair(ru_path=RU_PATH, en_path=EN_PATH, ru_changed=True),
        ru_text="Привет.\n",
        en_text=None,
    )

    with ExitStack() as stack:
        translator, heavy_qa, _prepare, _commit, _push = _workflow_patches(
            stack, github=github, client=client
        )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.load_pair_contents",
                return_value=[missing_en],
            )
        )
        run_doc_translate(
            repo_path=repo,
            github_repo="o/r",
            pr_number=SOURCE_PR,
            merge_base_with=base_sha,
            no_commit=True,
            config=_config(),
            _ops_ctx=ops,
        )

    translator.assert_called_once()
    heavy_qa.assert_called_once()
