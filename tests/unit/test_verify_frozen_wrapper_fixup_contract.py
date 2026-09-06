"""Real pair and real-Git doc_verify contracts for frozen wrapper fixups."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github import git_ops, workflow
from ydbdoc_review.github.provenance import (
    RuAuthority,
    TranslationArtifactProvenance,
    parse_authority_evidence,
    render_authority_evidence,
)
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.navigation.scope_planner import TranslationScopePlan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import classify_publication_blockers
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets

REPO_ID = "ydb-platform/ydb"
SOURCE_PR = 40385
TRANSLATION_PR = 52330
BRANCH = "ydbdoc-review/pr-40385"
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/pr52330-wrapper-repair-snapshots.json"
)
RU_PATHS = (
    "ydb/docs/ru/core/reference/configuration/auth_config.md",
    "ydb/docs/ru/core/reference/configuration/client_certificate_authorization.md",
    "ydb/docs/ru/core/reference/configuration/monitoring_config.md",
    "ydb/docs/ru/core/reference/configuration/tls.md",
    "ydb/docs/ru/core/reference/ydb-cli/_includes/connect.md",
    "ydb/docs/ru/core/security/authentication.md",
    "ydb/docs/ru/core/security/authorization.md",
    "ydb/docs/ru/core/security/index.md",
)
EN_PATHS = tuple(path.replace("/ru/", "/en/", 1) for path in RU_PATHS)
RU_CLIENT = RU_PATHS[1]
EN_CLIENT = EN_PATHS[1]
LABEL = "registering dynamic nodes"
FROZEN_HREF = (
    "../../devops/concepts/node-authorization.md"
    "#enabling-the-node-authentication-and-authorization-mode"
)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _mock_client() -> SimpleNamespace:
    return SimpleNamespace(usage_tracker=UsageTracker(), transcript_recorder=None)


def _critic_ok(*_args: object, **_kwargs: object) -> CriticResponse:
    return CriticResponse(verdict="ok", issues=[])


def _config():
    return load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "wrapper-fixup",
            "YDBDOC_YC_API_KEY": "test",
            "GITHUB_TOKEN": "api-test",
            "GITHUB_PUSH_TOKEN": "push-test",
            "YDBDOC_TRANSLATION_RU_AUTHORITY_MODE": "source-preserving",
            "YDBDOC_SKIP_OPS_GATES": "1",
        }
    )


def test_real_pair_current_is_unsafe_and_repaired_pair_is_safe() -> None:
    payload = _fixture()
    paths = payload["paths"]
    trees = payload["trees"]
    catalog = {
        (item["role"], item["path"])
        for item in payload["capture"]["pair_read_catalog"]
    }
    calls: list[tuple[str, str]] = []

    def read(path: str) -> str | None:
        role = "B" if path == paths["redirects"] else "K"
        calls.append((role, path))
        assert (role, path) in catalog, f"unknown frozen reader lookup: {role}:{path}"
        return trees[role][path]["text"]

    h0 = trees["H0"][paths["ru_clientcert"]]["text"]
    h = trees["H"][paths["ru_clientcert"]]["text"]
    b = trees["B"][paths["en_clientcert"]]["text"]
    k = trees["K"][paths["en_clientcert"]]["text"]
    p = k.replace(LABEL, f"[{LABEL}]({FROZEN_HREF})", 1)
    pair = DocPair(ru_path=RU_CLIENT, en_path=EN_CLIENT, ru_changed=True)
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=RU_CLIENT,
        target_path=EN_CLIENT,
        source_lang="ru",
        target_lang="en",
        summary="real pinned pair",
    )
    ctx = HarnessContext.from_options(
        _mock_client(),
        glossary=Glossary(entries=[]),
        config=_config(),
        en_toc_reachable=frozenset(payload["capture"]["reachable"]),
        docs_text_reader=read,
        docs_repo_path=None,
    )
    with (
        patch("ydbdoc_review.harness.steps.run_critic_pass", side_effect=_critic_ok),
        patch("ydbdoc_review.harness.steps.run_verify", side_effect=_critic_ok),
    ):
        current = run_pair_plan(
            PairContent(
                pair=pair,
                ru_text=h,
                ru_base_text=h0,
                en_text=k,
                en_base_text=b,
            ),
            plan,
            ctx,
            {},
        )
        repaired = run_pair_plan(
            PairContent(
                pair=pair,
                ru_text=h,
                ru_base_text=h0,
                en_text=p,
                en_base_text=b,
            ),
            plan,
            ctx,
            {},
        )

    assert current.target_text == k
    assert [issue.code for issue in current.validation_issues] == [
        "missing_link_wrapper"
    ]
    assert current.file_result is not None
    assert current.file_result.link_contract_issues == ()
    assert current.file_result.heuristic_blocking == []
    assert classify_publication_blockers(
        PRTranslationResult(pair_results=[current])
    ).unsafe
    assert repaired.target_text == p
    assert repaired.validation_issues == ()
    assert repaired.file_result is not None
    assert repaired.file_result.link_contract_issues == ()
    assert repaired.file_result.heuristic_blocking == []
    assert not classify_publication_blockers(
        PRTranslationResult(pair_results=[repaired])
    ).any
    assert all("/ru/" not in path for _role, path in calls)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_bare(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="")


def _auth_anchors(path: str) -> str:
    if not path.endswith("/security/authentication.md"):
        return ""
    return (
        "\n## Device interfaces {#device-auth-interfaces}\n"
        "\n## Client certificates {#client-certificate}\n"
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _text_at(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
    )
    return None if proc.returncode else proc.stdout.decode()


@dataclass
class History:
    repo: Path
    upstream: Path
    h0: str
    h: str
    b: str
    c: str
    k: str
    body: str


@pytest.fixture
def wrapper_history(tmp_path: Path) -> History:
    payload = _fixture()
    paths = payload["paths"]
    trees = payload["trees"]
    repo = tmp_path / "checkout"
    upstream = tmp_path / "upstream.git"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "wrapper@example.test")
    _git(repo, "config", "user.name", "Wrapper Contract")

    _write(repo, RU_CLIENT, trees["H0"][paths["ru_clientcert"]]["text"])
    for index, path in enumerate(RU_PATHS):
        if path != RU_CLIENT:
            _write(
                repo,
                path,
                f"# Источник {index}\n\nСтарый текст {index}.\n"  # noqa: RUF001
                + _auth_anchors(path),
            )
    h0 = _commit(repo, "H0 source base")

    _write(repo, RU_CLIENT, trees["H"][paths["ru_clientcert"]]["text"])
    for index, path in enumerate(RU_PATHS):
        if path != RU_CLIENT:
            _write(
                repo,
                path,
                f"# Источник {index}\n\nНовый текст {index}.\n"  # noqa: RUF001
                + _auth_anchors(path),
            )
    h = _commit(repo, "H source head")
    _git(repo, "branch", "source-40385", h)

    _write(repo, EN_CLIENT, trees["B"][paths["en_clientcert"]]["text"])
    for index, path in enumerate(EN_PATHS):
        if path != EN_CLIENT:
            _write(
                repo,
                path,
                f"# Target {index}\n\nOld text {index}.\n" + _auth_anchors(path),
            )
    for path, entry in trees["K"].items():
        if path not in EN_PATHS:
            _write(repo, path, entry["text"])
    _write(repo, paths["redirects"], trees["B"][paths["redirects"]]["text"])
    config_hub = "ydb/docs/en/core/reference/configuration/index.md"
    _write(repo, config_hub, "# Configuration\n")
    toc_targets = sorted(set(EN_PATHS) | set(trees["K"]) | {config_hub})
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n"
        + "".join(
            f"  - name: p{index}\n    href: {path.removeprefix('ydb/docs/en/core/')}\n"
            for index, path in enumerate(toc_targets)
        ),
    )
    b = _commit(repo, "B frozen English baseline")

    _write(repo, EN_CLIENT, trees["K"][paths["en_clientcert"]]["text"])
    for index, path in enumerate(EN_PATHS):
        if path != EN_CLIENT:
            _write(
                repo,
                path,
                f"# Target {index}\n\nTranslated text {index}.\n"
                + _auth_anchors(path),
            )
    c = _commit(repo, "C root translation artifact")
    cli_path = EN_PATHS[4]
    cli_text = _text_at(repo, c, cli_path)
    assert isinstance(cli_text, str)
    assert cli_text.count("Translated text 4.") == 1
    _write(
        repo,
        cli_path,
        cli_text.replace("Translated text 4.", "Verified text 4.", 1),
    )
    k = _commit(repo, "K verified descendant")

    provenance = TranslationArtifactProvenance(
        authority=RuAuthority(
            source_repo=REPO_ID,
            source_pr=SOURCE_PR,
            source_base_sha=h0,
            source_head_sha=h,
            baseline_sha=b,
            ru_sha=h,
            mode=RuAuthorityMode.SOURCE_PRESERVING,
        ),
        candidate_sha=c,
    )
    body = "Wrapper test\n\n" + render_authority_evidence(provenance) + "\n"

    _git(repo, "init", "--bare", str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    _git(repo, "push", "origin", f"{b}:refs/heads/main")
    _git(repo, "push", "origin", f"{h}:refs/heads/source-40385")
    _git(repo, "push", "origin", f"{k}:refs/heads/{BRANCH}")
    _git(repo, "fetch", "origin")
    _git(repo, "checkout", "--detach", k)
    return History(repo, upstream, h0, h, b, c, k, body)


@dataclass
class FakeGitHub:
    history: History
    remote_head: str
    body: str
    comments: list[tuple[str, str]] = field(default_factory=list)
    draft_calls: int = 0
    lease_conflict_sha: str | None = None

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == TRANSLATION_PR
        return {
            "number": number,
            "title": "source-preserving translation",
            "body": self.body,
            "merged": False,
            "state": "open",
            "merge_commit_sha": None,
            "head": {
                "ref": BRANCH,
                "sha": self.remote_head,
                "repo": {
                    "clone_url": f"https://github.com/{REPO_ID}.git",
                    "full_name": REPO_ID,
                    "owner": {"login": "ydb-platform"},
                    "name": "ydb",
                },
            },
            "base": {"ref": "main", "sha": self.history.b},
        }

    def iter_pull_files(
        self, owner: str, repo: str, number: int
    ) -> Iterator[dict[str, str]]:
        assert f"{owner}/{repo}" == REPO_ID and number == TRANSLATION_PR
        output = _git_bare(
            self.history.upstream,
            "diff",
            "--name-status",
            self.history.b,
            self.remote_head,
        )
        for line in output.splitlines():
            status, path = line.split("\t", 1)
            yield {
                "filename": path,
                "status": "added" if status == "A" else "removed" if status == "D" else "modified",
            }

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str | None:
        assert f"{owner}/{repo}" == REPO_ID and branch == BRANCH
        return self.remote_head

    def convert_pull_to_draft(self, owner: str, repo: str, number: int) -> None:
        assert f"{owner}/{repo}" == REPO_ID and number == TRANSLATION_PR
        self.draft_calls += 1

    def update_pull_body(self, owner: str, repo: str, number: int, body: str) -> None:
        assert f"{owner}/{repo}" == REPO_ID and number == TRANSLATION_PR
        self.body = body

    def iter_issue_comments(
        self, owner: str, repo: str, number: int
    ) -> Iterator[dict[str, object]]:
        assert f"{owner}/{repo}" == REPO_ID and number == TRANSLATION_PR
        return iter(())

    def post_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        assert f"{owner}/{repo}" == REPO_ID and number == TRANSLATION_PR
        self.comments.append((self.remote_head, body))
        return f"https://github.com/{REPO_ID}/pull/{number}#comment"


@contextmanager
def _workflow_runtime(history: History, gh: FakeGitHub) -> Iterator[dict[str, object]]:
    scope = TranslationScopePlan(
        doc_ru_paths=frozenset(RU_PATHS),
        doc_from_diff=frozenset(RU_PATHS),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(),
    )
    real_prepare = workflow.prepare_translation_branch_on_base
    real_apply = workflow._apply_results_to_disk
    observations: dict[str, object] = {
        "pushes": [],
        "apply_calls": 0,
        "apply_events": [],
    }

    def prepare(repo_path: str, **kwargs: object) -> None:
        forwarded = dict(kwargs)
        forwarded["base_remote_url"] = str(history.upstream)
        real_prepare(repo_path, **forwarded)

    def local_push(
        repo_path: str,
        _remote_name: str,
        branch: str,
        _token: str,
        _url: str,
        *,
        expected_remote_sha: str | None = None,
        source_sha: str | None = None,
        **_kwargs: object,
    ) -> git_ops.RefMutationReceipt:
        assert branch == BRANCH and source_sha is not None
        if gh.lease_conflict_sha is not None:
            gh.remote_head = gh.lease_conflict_sha
            raise RuntimeError("simulated destination lease conflict")
        assert gh.remote_head == expected_remote_sha
        resolved = _git(Path(repo_path), "rev-parse", f"{source_sha}^{{commit}}")
        _git(
            Path(repo_path),
            "push",
            "--force",
            str(history.upstream),
            f"{resolved}:refs/heads/{BRANCH}",
        )
        gh.remote_head = resolved
        observations["pushes"].append(resolved)
        return git_ops.RefMutationReceipt(
            lease=git_ops.RemoteRefLease(
                branch=BRANCH, expected_sha=expected_remote_sha
            ),
            operation=git_ops.RefMutationOperation.UPDATE,
            requested_sha=resolved,
            status=git_ops.RefMutationStatus.CHANGED,
            porcelain_flag="+",
            stdout="local guarded push",
            stderr="",
        )

    def observed_apply(*args: object, **kwargs: object):
        observations["apply_calls"] += 1
        head_before = _git(history.repo, "rev-parse", "HEAD")
        remote_before = gh.remote_head
        pushes_before = list(observations["pushes"])
        status_before = _git(
            history.repo, "status", "--porcelain", "--untracked-files=all"
        )
        with patch.object(workflow, "write_text", wraps=workflow.write_text) as writer:
            touched = real_apply(*args, **kwargs)
        observations["apply_events"].append(
            {
                "head_before": head_before,
                "remote_before": remote_before,
                "pushes_before": pushes_before,
                "status_before": status_before,
                "head_after": _git(history.repo, "rev-parse", "HEAD"),
                "remote_after": gh.remote_head,
                "pushes_after": list(observations["pushes"]),
                "status_after": _git(
                    history.repo, "status", "--porcelain", "--untracked-files=all"
                ),
                "written": list(touched.written),
                "deleted": list(touched.deleted),
                "writer_calls": writer.call_count,
            }
        )
        return touched

    with ExitStack() as stack:
        stack.enter_context(patch.object(workflow, "GitHubClient", return_value=gh))
        stack.enter_context(
            patch.object(workflow, "create_llm_client", return_value=_mock_client())
        )
        stack.enter_context(
            patch.object(workflow, "load_glossary", return_value=Glossary(entries=[]))
        )
        stack.enter_context(patch.object(workflow, "plan_translation_scope", return_value=scope))
        stack.enter_context(
            patch.object(
                workflow,
                "_reconstruct_late_dependency_budget_for_verify",
                return_value=frozenset(),
            )
        )
        stack.enter_context(
            patch("ydbdoc_review.harness.steps.run_critic_pass", side_effect=_critic_ok)
        )
        stack.enter_context(
            patch("ydbdoc_review.harness.steps.run_verify", side_effect=_critic_ok)
        )
        stack.enter_context(
            patch.object(workflow, "prepare_translation_branch_on_base", side_effect=prepare)
        )
        stack.enter_context(patch.object(workflow, "push_branch", side_effect=local_push))
        stack.enter_context(
            patch.object(workflow, "_apply_results_to_disk", side_effect=observed_apply)
        )
        stack.enter_context(
            patch.object(workflow, "apply_orphan_toc_page_checks", return_value=[])
        )
        yield observations


def test_workflow_fixture_has_only_the_source_owned_wrapper_blocker(
    wrapper_history: History,
) -> None:
    config_hub = "ydb/docs/en/core/reference/configuration/index.md"
    assert set(
        _git(
            wrapper_history.repo,
            "diff",
            "--name-only",
            wrapper_history.b,
            wrapper_history.k,
        ).splitlines()
    ) == set(EN_PATHS)
    hub_oids = {
        _git(wrapper_history.repo, "rev-parse", f"{ref}:{config_hub}")
        for ref in (wrapper_history.b, wrapper_history.c, wrapper_history.k)
    }
    assert len(hub_oids) == 1

    gh = FakeGitHub(wrapper_history, wrapper_history.k, wrapper_history.body)
    with _workflow_runtime(wrapper_history, gh) as observed:
        job = workflow.run_doc_verify(
            repo_path=str(wrapper_history.repo),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            dry_run=True,
            config=_config(),
            skip_ops_gates=True,
        )
    assert observed["pushes"] == []
    assert gh.remote_head == wrapper_history.k
    assert len(job.pr_result.pair_results) == 8
    assert job.pr_result.completeness_gaps == []
    assert job.pr_result.final_tree_blockers == []
    client_runs = []
    for run in job.pr_result.pair_results:
        assert run.error is None
        assert run.file_result is not None
        assert run.file_result.link_contract_issues == ()
        assert run.file_result.heuristic_blocking == []
        if run.plan.target_path == EN_CLIENT:
            client_runs.append(run)
        else:
            assert run.validation_issues == ()
    assert len(client_runs) == 1
    assert [issue.code for issue in client_runs[0].validation_issues] == [
        "missing_link_wrapper"
    ]
    assert classify_publication_blockers(job.pr_result).unsafe

    k_text = _text_at(wrapper_history.repo, wrapper_history.k, EN_CLIENT)
    assert isinstance(k_text, str)
    proposed = k_text.replace(LABEL, f"[{LABEL}]({FROZEN_HREF})", 1)
    assert check_en_page_link_targets(
        EN_CLIENT,
        proposed,
        read_text=lambda path: _text_at(
            wrapper_history.repo, wrapper_history.k, path
        ),
        baseline_read_text=lambda path: _text_at(
            wrapper_history.repo, wrapper_history.b, path
        ),
    ) == []


def test_real_git_verify_publishes_one_wrapper_and_freshly_verifies_k2(
    wrapper_history: History,
) -> None:
    gh = FakeGitHub(wrapper_history, wrapper_history.k, wrapper_history.body)
    before_blobs = {
        path: _git(wrapper_history.repo, "rev-parse", f"{wrapper_history.k}:{path}")
        for path in EN_PATHS
    }
    with _workflow_runtime(wrapper_history, gh) as observed:
        job = workflow.run_doc_verify(
            repo_path=str(wrapper_history.repo),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_config(),
            skip_ops_gates=True,
        )

    assert len(observed["pushes"]) == 1
    k2 = observed["pushes"][0]
    assert _git(wrapper_history.repo, "rev-parse", f"{k2}^") == wrapper_history.k
    assert _git(wrapper_history.repo, "diff", "--name-only", wrapper_history.k, k2) == EN_CLIENT
    assert _git(wrapper_history.repo, "diff", "--numstat", wrapper_history.k, k2) == f"1\t1\t{EN_CLIENT}"
    after = _text_at(wrapper_history.repo, k2, EN_CLIENT)
    before = _text_at(wrapper_history.repo, wrapper_history.k, EN_CLIENT)
    assert after == before.replace(LABEL, f"[{LABEL}]({FROZEN_HREF})", 1)
    for path in set(EN_PATHS) - {EN_CLIENT}:
        assert _git(wrapper_history.repo, "rev-parse", f"{k2}:{path}") == before_blobs[path]
    evidence = parse_authority_evidence(gh.body)
    assert evidence.candidate_sha == wrapper_history.c
    assert evidence.authority.source_head_sha == wrapper_history.h
    assert evidence.authority.source_base_sha == wrapper_history.h0
    assert evidence.authority.baseline_sha == wrapper_history.b
    assert gh.remote_head == k2
    assert gh.comments and gh.comments[-1][0] == k2
    assert k2[:12] in gh.comments[-1][1]
    assert "можно мержить" in gh.comments[-1][1]
    assert not classify_publication_blockers(job.pr_result).any
    assert not workflow.job_requires_nonzero_exit(job)
    assert observed["apply_calls"] == 1
    assert observed["apply_events"] == [
        {
            "head_before": k2,
            "remote_before": k2,
            "pushes_before": [k2],
            "status_before": "",
            "head_after": k2,
            "remote_after": k2,
            "pushes_after": [k2],
            "status_after": "",
            "written": [],
            "deleted": [],
            "writer_calls": 0,
        }
    ]


@pytest.mark.parametrize(
    "mode",
    ["dry_run", "no_commit", "depth3"],
)
def test_nonpublishing_modes_keep_original_typed_blocker_visible(
    wrapper_history: History, mode: str
) -> None:
    gh = FakeGitHub(wrapper_history, wrapper_history.k, wrapper_history.body)
    kwargs: dict[str, object] = {}
    if mode == "dry_run":
        kwargs["dry_run"] = True
    elif mode == "no_commit":
        kwargs["no_commit"] = True
    else:
        kwargs["_fixup_rerun_depth"] = 3
    with _workflow_runtime(wrapper_history, gh) as observed:
        job = workflow.run_doc_verify(
            repo_path=str(wrapper_history.repo),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_config(),
            skip_ops_gates=True,
            **kwargs,
        )
    assert observed["pushes"] == []
    assert gh.remote_head == wrapper_history.k
    assert classify_publication_blockers(job.pr_result).unsafe
    if mode != "dry_run":
        assert gh.comments
        assert "missing_link_wrapper" in gh.comments[-1][1]


def test_lease_conflict_does_not_clobber_or_report_unconfirmed_k2(
    wrapper_history: History,
) -> None:
    other = _git(wrapper_history.repo, "commit-tree", f"{wrapper_history.k}^{{tree}}", "-p", wrapper_history.k, "-m", "E2")
    gh = FakeGitHub(
        wrapper_history,
        wrapper_history.k,
        wrapper_history.body,
        lease_conflict_sha=other,
    )
    with _workflow_runtime(wrapper_history, gh):
        with pytest.raises(RuntimeError, match="lease conflict"):
            workflow.run_doc_verify(
                repo_path=str(wrapper_history.repo),
                github_repo=REPO_ID,
                pr_number=TRANSLATION_PR,
                merge_base_with="origin/main",
                config=_config(),
                skip_ops_gates=True,
            )
    assert gh.remote_head == other
    assert not any("можно мержить" in body for _sha, body in gh.comments)
