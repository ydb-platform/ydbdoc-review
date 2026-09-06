"""RED contract for preparing candidate branches from a frozen commit."""

from __future__ import annotations

import inspect
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github import git_ops
from ydbdoc_review.github.pr import (
    PullRequestContext,
    translation_pr_base,
    verify_fixup_pr_base,
)
from ydbdoc_review.github.provenance import RuAuthority, TranslationArtifactProvenance
from ydbdoc_review.github.workflow import run_doc_translate, run_doc_verify
from ydbdoc_review.ops import lifecycle
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult, PairRunResult, PRTranslationResult


@pytest.fixture(autouse=True)
def _isolate_ops_ledger_and_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """This suite must remain a local-Git contract, with no external factories."""
    monkeypatch.setenv("YDBDOC_SKIP_OPS_GATES", "1")

    def _unexpected_external_factory(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external ledger/transcript factory reached from local Git contract")

    monkeypatch.setattr(lifecycle, "create_runs_ledger", _unexpected_external_factory)
    monkeypatch.setattr(lifecycle, "create_transcript_store", _unexpected_external_factory)


def _config():
    return load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "test-folder",
            "YDBDOC_YC_API_KEY": "test-key",
            "GITHUB_TOKEN": "test-token",
            "GITHUB_PUSH_TOKEN": "test-push-token",
            "YDBDOC_SKIP_OPS_GATES": "1",
        }
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@dataclass(frozen=True)
class FrozenRemote:
    checkout: Path
    upstream: Path
    base0: str
    base1: str
    head0: str
    head1: str


@pytest.fixture
def frozen_remote(tmp_path: Path) -> FrozenRemote:
    """One checkout and a local bare upstream with independently movable refs."""
    seed = tmp_path / "seed"
    upstream = tmp_path / "upstream.git"
    checkout = tmp_path / "checkout"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "test")
    _write(seed, "sentinel.txt", "B0\n")
    _write(seed, "ydb/docs/ru/a.md", "RU B0\n")
    _write(seed, "ydb/docs/en/a.md", "EN B0\n")
    _write(seed, "delete.md", "delete from frozen parent\n")
    base0 = _commit(seed, "B0")
    _git(seed, "init", "--bare", str(upstream))
    _git(seed, "remote", "add", "origin", str(upstream))
    _git(seed, "push", "origin", "main")

    _git(seed, "checkout", "-b", "feature/docs")
    _write(seed, "sentinel.txt", "C0\n")
    head0 = _commit(seed, "C0")
    _git(seed, "push", "origin", "feature/docs")
    _git(seed, "checkout", "main")
    _write(seed, "sentinel.txt", "B1\n")
    base1 = _commit(seed, "B1")
    _git(seed, "push", "origin", "main")
    _git(seed, "checkout", "feature/docs")
    _write(seed, "sentinel.txt", "C1\n")
    head1 = _commit(seed, "C1")
    _git(seed, "push", "origin", "feature/docs")

    _git(tmp_path, "clone", "--no-single-branch", str(upstream), str(checkout))
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "test")
    return FrozenRemote(checkout, upstream, base0, base1, head0, head1)


def _move_remote_ref(remote: FrozenRemote, branch: str, sha: str) -> None:
    subprocess.run(
        ["git", "--git-dir", str(remote.upstream), "update-ref", f"refs/heads/{branch}", sha],
        check=True,
        capture_output=True,
    )


def _move_checkout_ref(remote: FrozenRemote, branch: str, sha: str) -> None:
    """Move a local B/C name after the runner has frozen P, during model work."""
    _git(remote.checkout, "update-ref", f"refs/heads/{branch}", sha)


def _checkout_source_at_c0_with_main_at_b0(remote: FrozenRemote) -> None:
    """Reproduce the runner topology: attached source HEAD=C0, main=B0."""
    repo = remote.checkout
    _git(repo, "checkout", "-f", "--detach", remote.base0)
    _git(repo, "branch", "-f", "main", remote.base0)
    _git(repo, "checkout", "-f", "-B", "feature/docs", remote.head0)
    assert _git(repo, "rev-parse", "HEAD") == remote.head0
    assert _git(repo, "rev-parse", "main") == remote.base0


def _prepare_from_frozen_parent(
    remote: FrozenRemote,
    *,
    frozen_parent: str,
    moving_branch: str,
    moved_to: str,
    candidate: str,
) -> None:
    """Exercise real staging, checkout, overlay restoration and deletion.

    Production is expected to accept ``base_commit_sha``.  Until it does, this
    invokes the legacy branch-only API, which still gives a behavioral RED:
    its post-freeze fetch resolves the moved branch, rather than P.
    """
    repo = remote.checkout
    _git(repo, "checkout", "-f", "main")
    _write(repo, "overlay.md", f"overlay retained from {frozen_parent}\n")
    (repo / "delete.md").unlink()
    _move_remote_ref(remote, moving_branch, moved_to)

    kwargs: dict[str, object] = {
        "translation_branch": candidate,
        "base_remote_url": str(remote.upstream),
        "base_remote_name": "test-upstream",
        "base_branch": moving_branch,
        "paths": ["overlay.md"],
        "deleted_paths": ["delete.md"],
    }
    if "base_commit_sha" in inspect.signature(git_ops.prepare_translation_branch_on_base).parameters:
        kwargs["base_commit_sha"] = frozen_parent
    git_ops.prepare_translation_branch_on_base(str(repo), **kwargs)  # type: ignore[arg-type]
    committed = git_ops.git_commit_paths(
        str(repo),
        ["overlay.md"],
        "candidate from frozen P",
        "test",
        "test@example.com",
        deleted_paths=["delete.md"],
    )
    assert committed is True


@pytest.mark.parametrize(
    ("route", "frozen_kind", "moving_branch", "moved_kind", "pr_base"),
    [
        ("translate same-repo open", "C", "feature/docs", "C1", "ctx.head_ref"),
        ("translate fork open", "B", "main", "B1", "ctx.base_ref"),
        ("translate merged", "B", "main", "B1", "ctx.base_ref"),
        ("verify inline translation", "C", "feature/docs", "C1", "no PR"),
        ("verify separate same-repo open", "C", "feature/docs", "C1", "ctx.base_ref"),
        ("verify separate fork or merged", "B", "main", "B1", "ctx.base_ref"),
    ],
)
def test_candidate_commit_keeps_the_parent_frozen_before_prepare(
    frozen_remote: FrozenRemote,
    route: str,
    frozen_kind: str,
    moving_branch: str,
    moved_kind: str,
    pr_base: str,
):
    """Catch re-fetching B/C after snapshot P for every publication route."""
    frozen_parent = frozen_remote.head0 if frozen_kind == "C" else frozen_remote.base0
    moved_to = frozen_remote.head1 if moved_kind == "C1" else frozen_remote.base1
    candidate = f"candidate/{route.replace(' ', '-')}"

    _prepare_from_frozen_parent(
        frozen_remote,
        frozen_parent=frozen_parent,
        moving_branch=moving_branch,
        moved_to=moved_to,
        candidate=candidate,
    )

    repo = frozen_remote.checkout
    assert _git(repo, "rev-parse", "HEAD^") == frozen_parent, route
    assert _git(repo, "show", "HEAD:sentinel.txt") == ("C0" if frozen_kind == "C" else "B0")
    assert _git(repo, "show", "HEAD:overlay.md") == f"overlay retained from {frozen_parent}"
    assert subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", "HEAD:delete.md"],
        capture_output=True,
    ).returncode != 0
    # Keep the selected PR target visible in the table-driven failure output.
    assert pr_base in {"ctx.head_ref", "ctx.base_ref", "no PR"}


@pytest.mark.parametrize("bad_parent", ["unknown-parent", "HEAD^{tree}"], ids=["unknown", "noncommit"])
def test_prepare_rejects_invalid_frozen_parent_before_any_remote_mutation(
    frozen_remote: FrozenRemote,
    monkeypatch: pytest.MonkeyPatch,
    bad_parent: str,
):
    """An invalid P must not fetch, checkout, or silently fall back to main."""
    def unexpected_mutation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("prepare mutated or fetched before validating frozen P")

    monkeypatch.setattr(git_ops, "ensure_remote", unexpected_mutation)
    kwargs: dict[str, object] = {
        "translation_branch": "candidate/invalid-p",
        "base_remote_url": str(frozen_remote.upstream),
        "base_remote_name": "test-upstream",
        "base_branch": "main",
        "paths": [],
    }
    if "base_commit_sha" in inspect.signature(git_ops.prepare_translation_branch_on_base).parameters:
        kwargs["base_commit_sha"] = bad_parent
    else:
        kwargs["base_branch"] = bad_parent

    with pytest.raises(RuntimeError):
        git_ops.prepare_translation_branch_on_base(str(frozen_remote.checkout), **kwargs)  # type: ignore[arg-type]


def test_prepare_propagates_frozen_parent_resolution_error_before_any_remote_mutation(
    frozen_remote: FrozenRemote,
    monkeypatch: pytest.MonkeyPatch,
):
    """Injected P-resolution failures are errors, never a current-ref fallback."""
    def injected_failure(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("injected frozen-parent resolution failure")

    def unexpected_mutation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("prepare mutated or fetched after P resolution failed")

    monkeypatch.setattr(git_ops, "resolve_commit_ref", injected_failure, raising=False)
    monkeypatch.setattr(git_ops, "ensure_remote", unexpected_mutation)
    kwargs: dict[str, object] = {
        "translation_branch": "candidate/injected-p-error",
        "base_remote_url": str(frozen_remote.upstream),
        "base_remote_name": "test-upstream",
        "base_branch": "main",
        "paths": [],
    }
    if "base_commit_sha" in inspect.signature(git_ops.prepare_translation_branch_on_base).parameters:
        kwargs["base_commit_sha"] = frozen_remote.base0
    else:
        kwargs["base_branch"] = frozen_remote.base0

    with pytest.raises(RuntimeError, match="injected frozen-parent resolution failure"):
        git_ops.prepare_translation_branch_on_base(str(frozen_remote.checkout), **kwargs)  # type: ignore[arg-type]


def _translation_result() -> PRTranslationResult:
    pair = DocPair("ydb/docs/ru/a.md", "ydb/docs/en/a.md", ru_changed=True)
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
                target_text="candidate EN\n",
                file_result=FileTranslationResult(
                    file_path=pair.en_path,
                    final_text="candidate EN\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="prepare-base-contract",
                ),
            )
        ]
    )


def _pull(
    *,
    head_ref: str,
    head_sha: str,
    base_ref: str,
    base_sha: str,
    fork: bool = False,
    merged: bool = False,
) -> dict:
    return {
        "title": "docs",
        "body": "",
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {
                "clone_url": "https://github.com/fork/docs.git" if fork else "https://github.com/o/r.git",
                "full_name": "fork/docs" if fork else "o/r",
            },
        },
        "base": {"ref": base_ref, "sha": base_sha},
        "merged": merged,
        "state": "closed" if merged else "open",
        "merge_commit_sha": head_sha if merged else None,
    }


def _translation_provenance(
    remote: FrozenRemote,
    *,
    candidate_sha: str,
) -> TranslationArtifactProvenance:
    """Coherent A05 seam fixture; A04 assertions remain about frozen P only."""
    return TranslationArtifactProvenance(
        RuAuthority(
            source_repo="o/r",
            source_pr=7,
            source_base_sha=remote.base0,
            source_head_sha=remote.head0,
            baseline_sha=remote.base0,
            ru_sha=remote.base0,
            mode=RuAuthorityMode.CURRENT,
        ),
        candidate_sha,
    )


@pytest.mark.parametrize(
    ("route", "mode", "frozen_kind", "moving_branch", "fork", "merged", "expected_pr_base"),
    [
        ("translate same-repo open", "translate", "C", "feature/docs", False, False, "feature/docs"),
        ("translate fork open", "translate", "B", "main", True, False, "main"),
        ("translate merged", "translate", "B", "main", False, True, "main"),
        ("verify inline translation", "verify-inline", "C", "feature/docs", False, False, None),
        ("verify separate same-repo open", "verify-separate", "C", "feature/docs", False, False, "main"),
        ("verify separate fork", "verify-separate", "B", "main", True, False, "main"),
        ("verify separate merged", "verify-separate", "B", "main", False, True, "main"),
    ],
)
def test_publication_call_sites_pass_the_captured_commit_as_prepare_parent(
    frozen_remote: FrozenRemote,
    route: str,
    mode: str,
    frozen_kind: str,
    moving_branch: str,
    fork: bool,
    merged: bool,
    expected_pr_base: str | None,
):
    """Freeze B/C before model work, then reject any late P resolution."""
    repo = frozen_remote.checkout
    captured = frozen_remote.head0 if frozen_kind == "C" else frozen_remote.base0
    assert frozen_remote.base0 != frozen_remote.head0
    assert moving_branch == ("feature/docs" if frozen_kind == "C" else "main")
    assert captured != (frozen_remote.head1 if frozen_kind == "C" else frozen_remote.base1)
    _checkout_source_at_c0_with_main_at_b0(frozen_remote)
    head_ref = "ydbdoc-review/pr-7" if mode == "verify-inline" else "feature/docs"
    api_head_sha = frozen_remote.head0 if mode == "translate" else captured
    pull = _pull(
        head_ref=head_ref,
        head_sha=api_head_sha,
        base_ref="main",
        base_sha=frozen_remote.base0,
        fork=fork,
        merged=merged,
    )
    gh = MagicMock()
    gh.get_pull.return_value = pull
    gh.get_branch_sha.return_value = captured
    gh.iter_issue_comments.return_value = iter(())
    gh.post_issue_comment.return_value = "comment-url"
    gh.find_open_pull_by_head.return_value = None
    gh.create_pull.return_value = ("https://github.com/o/r/pull/99", 99, True)
    result = _translation_result()

    def _move_ref_during_model(*_args: object, **_kwargs: object) -> PRTranslationResult:
        _move_checkout_ref(frozen_remote, "main", frozen_remote.base1)
        _move_checkout_ref(frozen_remote, "feature/docs", frozen_remote.head1)
        assert _git(repo, "rev-parse", "HEAD") == frozen_remote.head1
        assert _git(repo, "rev-parse", "main") == frozen_remote.base1
        return result

    provenance = _translation_provenance(frozen_remote, candidate_sha=frozen_remote.head0)
    with patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh), patch(
        "ydbdoc_review.github.workflow.begin_ops_job",
        return_value=(SimpleNamespace(recorder=None, continue_feedback=None), GateResult(ok=True), None),
    ), patch("ydbdoc_review.github.workflow.finish_ops_job"), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/ru/a.md" if mode == "translate" else "ydb/docs/en/a.md", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        return_value=[("ydb/docs/ru/a.md" if mode == "translate" else "ydb/docs/en/a.md", "modified")],
    ), patch("ydbdoc_review.github.workflow.create_llm_client", return_value=MagicMock()), patch(
        "ydbdoc_review.github.workflow.load_glossary", return_value=MagicMock()
    ), patch(
        "ydbdoc_review.github.workflow.run_pr_translation",
        side_effect=_move_ref_during_model,
    ), patch(
        "ydbdoc_review.github.workflow._run_verify_pairs",
        side_effect=_move_ref_during_model,
    ), patch(
        "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks", return_value=[]
    ), patch(
        "ydbdoc_review.github.workflow.apply_en_link_target_checks", return_value=[]
    ), patch(
        "ydbdoc_review.github.workflow.build_source_pr_comment", return_value="local contract"
    ), patch(
        "ydbdoc_review.github.workflow.build_full_report", return_value="local contract"
    ), patch(
        "ydbdoc_review.github.workflow.prepare_translation_branch_on_base"
    ) as prepare, patch(
        "ydbdoc_review.github.workflow.git_commit_paths", return_value=False
    ), patch(
        "ydbdoc_review.github.workflow.parse_authority_evidence",
        return_value=provenance,
    ), patch(
        "ydbdoc_review.github.workflow.validate_authority_evidence",
        return_value=provenance,
    ):
        if mode == "translate":
            run_doc_translate(
                repo_path=str(repo),
                github_repo="o/r",
                pr_number=7,
                merge_base_with="main",
                config=_config(),
            )
        else:
            run_doc_verify(
                repo_path=str(repo),
                github_repo="o/r",
                pr_number=7,
                merge_base_with="main",
                config=_config(),
                skip_ops_gates=True,
            )

    prepare.assert_called_once()
    assert prepare.call_args.kwargs["base_commit_sha"] == captured, route
    context = PullRequestContext(
        owner="o",
        repo="r",
        number=7,
        title="docs",
        head_ref=head_ref,
        head_sha=captured,
        head_repo_full_name="fork/docs" if fork else "o/r",
        head_repo_https_url="https://github.com/o/r.git",
        base_ref="main",
        merged=merged,
    )
    # Existing and new stacked translation PRs must retain this target metadata.
    if mode == "translate":
        assert translation_pr_base(context) == expected_pr_base
    elif mode == "verify-inline":
        assert expected_pr_base is None
    else:
        assert verify_fixup_pr_base(context, translation_branch_prefix="ydbdoc-review/pr-") == expected_pr_base


@pytest.mark.parametrize(
    ("mutation", "expected_kind", "mutant_parent_kind", "fork"),
    [
        ("always-B", "C", "B0", False),
        ("always-C", "B", "C0", True),
        ("late-resolve", "C", "HEAD-after-model", False),
    ],
)
def test_translate_workflow_parent_oracle_rejects_prepare_selection_mutants(
    frozen_remote: FrozenRemote,
    mutation: str,
    expected_kind: str,
    mutant_parent_kind: str,
    fork: bool,
):
    """Run the real translate route against a temporary prepare-selection mutant."""
    repo = frozen_remote.checkout
    _checkout_source_at_c0_with_main_at_b0(frozen_remote)
    expected = frozen_remote.head0 if expected_kind == "C" else frozen_remote.base0
    pull = _pull(
        head_ref="feature/docs",
        head_sha=frozen_remote.head0,
        base_ref="main",
        base_sha=frozen_remote.base0,
        fork=fork,
    )
    gh = MagicMock()
    gh.get_pull.return_value = pull
    gh.post_issue_comment.return_value = "comment-url"
    result = _translation_result()

    def _move_refs_during_model(*_args: object, **_kwargs: object) -> PRTranslationResult:
        _move_checkout_ref(frozen_remote, "main", frozen_remote.base1)
        _move_checkout_ref(frozen_remote, "feature/docs", frozen_remote.head1)
        assert _git(repo, "rev-parse", "HEAD") == frozen_remote.head1
        return result

    def _mutant_prepare(
        repo_path: str,
        *,
        translation_branch: str,
        paths: list[str],
        deleted_paths: list[str] | None = None,
        **_kwargs: object,
    ) -> None:
        saved = {
            path: (Path(repo_path) / path).read_text(encoding="utf-8")
            for path in paths
        }
        parent = {
            "B0": frozen_remote.base0,
            "C0": frozen_remote.head0,
            "HEAD-after-model": _git(repo, "rev-parse", "HEAD"),
        }[mutant_parent_kind]
        git_ops.checkout_branch_at_ref(repo_path, translation_branch, parent)
        for path, text in saved.items():
            _write(Path(repo_path), path, text)
        git_ops.git_commit_paths(
            repo_path,
            paths,
            f"mutant {mutation}",
            "test",
            "test@example.com",
            deleted_paths=deleted_paths,
        )

    with patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh), patch(
        "ydbdoc_review.github.workflow.begin_ops_job",
        return_value=(SimpleNamespace(recorder=None, continue_feedback=None), GateResult(ok=True), None),
    ), patch("ydbdoc_review.github.workflow.finish_ops_job"), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_git",
        return_value=[("ydb/docs/ru/a.md", "modified")],
    ), patch(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        return_value=[("ydb/docs/ru/a.md", "modified")],
    ), patch("ydbdoc_review.github.workflow.create_llm_client", return_value=MagicMock()), patch(
        "ydbdoc_review.github.workflow.load_glossary", return_value=MagicMock()
    ), patch(
        "ydbdoc_review.github.workflow.run_pr_translation",
        side_effect=_move_refs_during_model,
    ), patch(
        "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks", return_value=[]
    ), patch(
        "ydbdoc_review.github.workflow.apply_en_link_target_checks", return_value=[]
    ), patch(
        "ydbdoc_review.github.workflow.build_source_pr_comment", return_value="local contract"
    ), patch(
        "ydbdoc_review.github.workflow.prepare_translation_branch_on_base",
        side_effect=_mutant_prepare,
    ), patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=False):
        run_doc_translate(
            repo_path=str(repo),
            github_repo="o/r",
            pr_number=7,
            merge_base_with="main",
            config=_config(),
        )

    with pytest.raises(AssertionError, match="wrong candidate parent"):
        assert _git(repo, "rev-parse", "HEAD^") == expected, "wrong candidate parent"
