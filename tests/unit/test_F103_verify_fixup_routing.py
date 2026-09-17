"""F-103: verify fixes route by branch ownership, not a name alone."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.git_ops import (
    RefMutationOperation,
    RefMutationReceipt,
    RefMutationStatus,
    RemoteRefLease,
)
from ydbdoc_review.github.provenance import RuAuthority, TranslationArtifactProvenance
from ydbdoc_review.github.workflow import run_doc_verify
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
)


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


def _result() -> PRTranslationResult:
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
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="Repaired.\n",
                file_result=FileTranslationResult(
                    file_path=pair.en_path,
                    final_text="Repaired.\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="v1",
                ),
            )
        ]
    )


@pytest.fixture
def repo(tmp_path: Path) -> str:
    root = tmp_path / "repo"
    en = root / "ydb" / "docs" / "en"
    ru = root / "ydb" / "docs" / "ru"
    en.mkdir(parents=True)
    ru.mkdir(parents=True)
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")
    (ru / "a.md").write_text("Привет.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "candidate"],
        cwd=root,
        check=True,
    )
    return str(root)


def _head(repo: str) -> str:
    return subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True
    ).strip()


def _receipt(branch: str, expected: str | None, requested: str) -> RefMutationReceipt:
    return RefMutationReceipt(
        lease=RemoteRefLease(branch, expected),
        operation=RefMutationOperation.UPDATE,
        requested_sha=requested,
        status=RefMutationStatus.CHANGED,
        porcelain_flag="*",
        stdout="",
        stderr="",
    )


def _run(repo: str, *, head_ref: str, head_repo: str, title: str = "docs"):
    source_number = 11
    candidate = _head(repo)
    source_pull = {
        "title": title,
        "body": "",
        "head": {
            "ref": head_ref,
            "sha": candidate,
            "repo": {
                "clone_url": f"https://github.com/{head_repo}.git",
                "full_name": head_repo,
            },
        },
        "base": {"ref": "main", "sha": candidate},
        "user": {
            "login": "github-actions[bot]" if title != "docs" else "author"
        },
        "state": "open",
        "merged": False,
    }
    fixup_pull = {
        "title": f"Critic fixes for #{source_number}",
        "body": "",
        "head": {
            "ref": f"ydbdoc-review/verify-{source_number}",
            "sha": candidate,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": candidate},
        "state": "open",
        "merged": False,
    }
    remote: dict[str, str | None] = {
        f"ydbdoc-review/verify-{source_number}": None,
        head_ref: candidate if head_repo == "o/r" else None,
    }
    linked_source = int(head_ref.rsplit("-", 1)[1])
    provenance = TranslationArtifactProvenance(
        RuAuthority(
            source_repo="o/r",
            source_pr=linked_source,
            source_base_sha=candidate,
            source_head_sha=candidate,
            baseline_sha=candidate,
            ru_sha=candidate,
            mode=RuAuthorityMode.CURRENT,
        ),
        candidate,
    )

    with patch("ydbdoc_review.github.workflow.GitHubClient") as github_cls, patch(
        "ydbdoc_review.github.workflow.begin_ops_job",
        return_value=(None, GateResult(ok=True), None),
    ), patch("ydbdoc_review.github.workflow.create_llm_client") as create_llm, patch(
        "ydbdoc_review.github.workflow._run_verify_pairs", return_value=_result()
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/en/a.md", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        return_value=[("ydb/docs/en/a.md", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.parse_authority_evidence",
        return_value=object(),
    ), patch(
        "ydbdoc_review.github.workflow.validate_authority_evidence",
        return_value=provenance,
    ), patch(
        "ydbdoc_review.github.workflow.commit_changes_between",
        return_value=[("ydb/docs/ru/a.md", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.prepare_translation_branch_on_base"
    ) as prepare, patch(
        "ydbdoc_review.github.workflow.git_commit_paths", return_value=True
    ), patch("ydbdoc_review.github.workflow.push_branch") as push:
        gh = github_cls.return_value
        create_llm.return_value.usage_tracker = None
        gh.get_pull.side_effect = lambda _owner, _repo, number: (
            source_pull if number == source_number else fixup_pull
        )
        gh.get_branch_sha.side_effect = (
            lambda _owner, _repo, branch: remote.get(branch)
        )

        def _push(*args, **kwargs):
            branch = args[2]
            requested = kwargs["source_sha"]
            remote[branch] = requested
            fixup_pull["head"]["sha"] = requested
            return _receipt(branch, kwargs["expected_remote_sha"], requested)

        push.side_effect = _push
        gh.create_pull.return_value = (
            "https://github.com/o/r/pull/99",
            99,
            True,
        )
        gh.iter_issue_comments.return_value = iter([])
        gh.post_issue_comment.side_effect = (
            lambda _owner, _repo, number, _body: f"comment-{number}"
        )

        job = run_doc_verify(
            repo_path=repo,
            github_repo="o/r",
            pr_number=source_number,
            merge_base_with="HEAD",
            config=_config(),
        )

    return job, gh, prepare, push


@pytest.mark.parametrize(
    ("head_ref", "title"),
    [
        ("ydbdoc-review/pr-7", "Auto-translate docs from PR #7"),
        ("ydbdoc-review/verify-11", "Critic fixes for #11"),
    ],
)
def test_F103_service_branch(repo: str, head_ref: str, title: str) -> None:
    job, gh, prepare, push = _run(
        repo,
        head_ref=head_ref,
        head_repo="o/r",
        title=title,
    )

    assert push.call_args.args[2] == head_ref
    assert prepare.call_args.kwargs["translation_branch"] == head_ref
    gh.create_pull.assert_not_called()
    assert job.translation_pr_number == 11
    assert [call.args[2] for call in gh.post_issue_comment.call_args_list] == [11]


@pytest.mark.parametrize("head_repo", ["contributor/r", "o/r"])
@pytest.mark.parametrize(
    "head_ref", ["ydbdoc-review/pr-7", "ydbdoc-review/verify-7"]
)
def test_F103_author_fork(repo: str, head_repo: str, head_ref: str) -> None:
    job, gh, prepare, push = _run(
        repo,
        head_ref=head_ref,
        head_repo=head_repo,
    )

    assert push.call_args.args[2] == "ydbdoc-review/verify-11"
    assert prepare.call_args.kwargs["translation_branch"] == "ydbdoc-review/verify-11"
    assert push.call_args.args[2] != head_ref
    create_kwargs = gh.create_pull.call_args.kwargs
    assert create_kwargs["title"] == "Critic fixes for #11"
    assert create_kwargs["head"] == "ydbdoc-review/verify-11"
    assert create_kwargs["base"] == "main"
    assert create_kwargs["draft"] is False
    assert "Auto-generated critic fixes for [o/r#11]" in create_kwargs["body"]
    assert "full ``doc_verify`` QA report is posted on **this** PR" in create_kwargs["body"]
    assert job.translation_pr_number == 99
    report_call, summary_call = gh.post_issue_comment.call_args_list
    assert report_call.args[2] == 99
    assert "## Статус QA (K):" in report_call.args[3]
    assert summary_call.args[2] == 11
    assert "полный QA-отчёт" in summary_call.args[3]
    assert "#99" in summary_call.args[3]
    assert "## Статус QA (K):" not in summary_call.args[3]
