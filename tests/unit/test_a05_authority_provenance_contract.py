"""A05 RED contract for explicit RU authority and durable provenance.

The contract intentionally stays above the implementation seam.  It drives the
real workflow entrypoints, scope planner, pair loaders, disk apply, local Git
commit, report builders, evidence reload, deterministic final-tree checks, and
one inline verify recursion.  Only GitHub transport and the LLM boundary are
replaced.  Assertions concern paths, bytes, commits, reports, and mutation
ordering, never proposed dataclass fields or proposed serializer function names.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github import git_ops, workflow
from ydbdoc_review.github.provenance import RuAuthority, TranslationArtifactProvenance
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.ops import lifecycle
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair, counterpart
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.translation.glossary import Glossary

REPO_ID = "ydb-platform/ydb"
SOURCE_PR = 40385
LATER_PR = 50704
TRANSLATION_PR = 940385
BILINGUAL_PR = 60003
OPEN_PR = 60002
RU_ROOT = "ydb/docs/ru/core"
EN_ROOT = "ydb/docs/en/core"

H_SEEDS = (
    f"{RU_ROOT}/reference/configuration/client_certificate_authorization.md",
    f"{RU_ROOT}/reference/configuration/monitoring_config.md",
    f"{RU_ROOT}/reference/configuration/tls.md",
    f"{RU_ROOT}/security/authentication.md",
    f"{RU_ROOT}/security/index.md",
)
H_DEPENDENCY = f"{RU_ROOT}/security/_assets/client-certificate-prerequisite.md"
H_NAV = f"{RU_ROOT}/security/toc_p.yaml"

L_ADDED = (
    f"{RU_ROOT}/security/caching-authentication-results.md",
    f"{RU_ROOT}/security/_assets/user-token.md",
    f"{RU_ROOT}/security/_assets/user-token-lifecycle.md",
)
L_MODIFIED = (
    f"{RU_ROOT}/security/authentication.md",
    f"{RU_ROOT}/reference/configuration/auth_config.md",
    f"{RU_ROOT}/concepts/glossary.md",
    f"{RU_ROOT}/security/authorization.md",
    f"{RU_ROOT}/security/toc_p.yaml",
)
L_PATHS = L_ADDED + L_MODIFIED
L_ONLY_PATHS = frozenset(L_PATHS) - frozenset((*H_SEEDS, H_NAV))
B_ARTIFACT = "artifacts/generated-translation-50704.json"
B1_ONLY_PATH = f"{RU_ROOT}/security/b1-only-edge.md"

EXPECTED_H_DOC_SCOPE = frozenset((*H_SEEDS, H_DEPENDENCY))
EXPECTED_H_NAV_SCOPE = frozenset((H_NAV,))
EXPECTED_H_EN_WRITES = frozenset(counterpart(path, "ydb/docs") for path in EXPECTED_H_DOC_SCOPE)
EXPECTED_H_EN_WRITES = frozenset(path for path in EXPECTED_H_EN_WRITES if path)
EXPECTED_H_EN_NAV = H_NAV.replace("/ru/", "/en/", 1)


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YDBDOC_SKIP_OPS_GATES", "1")

    def _external(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("A05 contract reached an external ledger or transcript store")

    monkeypatch.setattr(lifecycle, "create_runs_ledger", _external)
    monkeypatch.setattr(lifecycle, "create_transcript_store", _external)


def _config(*, ru_authority_mode: str | None = None):
    env = {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "a05-folder",
        "YDBDOC_YC_API_KEY": "a05-key",
        "GITHUB_TOKEN": "a05-api-token",
        "GITHUB_PUSH_TOKEN": "a05-push-token",
        "YDBDOC_SKIP_OPS_GATES": "1",
    }
    if ru_authority_mode is not None:
        env["YDBDOC_TRANSLATION_RU_AUTHORITY_MODE"] = ru_authority_mode
    return load_config(env=env)


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


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _text_at(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
    )
    if proc.returncode:
        return None
    return proc.stdout.decode("utf-8")


def _tree_paths(repo: Path, ref: str) -> tuple[str, ...]:
    out = _git(repo, "ls-tree", "-r", "--name-only", ref)
    return tuple(line for line in out.splitlines() if line)


def _name_status(repo: Path, before: str, after: str) -> tuple[tuple[str, str], ...]:
    out = _git(repo, "diff", "--name-status", before, after)
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        status, path = line.split("\t", 1)
        rows.append((status, path))
    return tuple(rows)


@dataclass
class AuthorityHistory:
    repo: Path
    upstream: Path
    h0: str
    h: str
    later: str
    baseline: str
    b1: str
    candidate: str | None = None

    def move_named_refs_after_freeze(self) -> None:
        _git(self.repo, "update-ref", "refs/heads/main", self.b1)
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.b1)
        _git(self.repo, "update-ref", "refs/heads/source-40385", self.later)
        _git(
            self.repo,
            "push",
            "--force",
            str(self.upstream),
            f"{self.b1}:refs/heads/main",
            f"{self.later}:refs/heads/source-40385",
        )


@pytest.fixture
def authority_history(tmp_path: Path) -> AuthorityHistory:
    repo = tmp_path / "checkout"
    upstream = tmp_path / "upstream.git"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a05@example.test")
    _git(repo, "config", "user.name", "A05 Contract")

    initial_ru = {
        H_SEEDS[0]: "# Client certificate authorization\n\nH0 client certificate prose.\n",
        H_SEEDS[1]: "# Monitoring configuration\n\nH0 monitoring prose.\n",
        H_SEEDS[2]: "# TLS settings {#tls-settings}\n\nH0 TLS prose.\n",
        H_SEEDS[3]: "# Authentication {#historic-auth}\n\nH0 authentication prose.\n",
        H_SEEDS[4]: "# Security\n\nH0 security index prose.\n",
        H_DEPENDENCY: (
            "# Shared client certificate prerequisite "
            "{#client-certificate-prerequisite}\n\n"
            "H-DEPENDENCY-SOURCE bytes that predate PR 40385.\n\n"
            "[Historical dependency blocker](missing-h-dependency.md#required-anchor)\n"
        ),
        L_MODIFIED[1]: "# Authentication configuration\n\nH0 auth config prose.\n",
        L_MODIFIED[2]: "# Glossary\n\nH0 glossary prose.\n",
        L_MODIFIED[3]: "# Authorization\n\nH0 authorization prose.\n",
    }
    for path, text in initial_ru.items():
        _write(repo, path, text)

    for path in H_SEEDS:
        en_path = counterpart(path, "ydb/docs")
        assert en_path is not None
        _write(repo, en_path, f"# EN baseline for {Path(path).name}\n\nH0 EN bytes.\n")

    _write(
        repo,
        f"{EN_ROOT}/toc_p.yaml",
        "items:\n"
        "  - name: Security\n"
        "    include:\n"
        "      mode: link\n"
        "      path: security/toc_p.yaml\n"
        "  - name: Client certificate authorization\n"
        "    href: reference/configuration/client_certificate_authorization.md\n"
        "  - name: Monitoring configuration\n"
        "    href: reference/configuration/monitoring_config.md\n"
        "  - name: TLS settings\n"
        "    href: reference/configuration/tls.md\n"
        "  - name: Authentication configuration\n"
        "    href: reference/configuration/auth_config.md\n"
        "  - name: Glossary\n"
        "    href: concepts/glossary.md\n"
        "  - name: Authorization\n"
        "    href: security/authorization.md\n",
    )
    _write(
        repo,
        H_NAV,
        "items:\n"
        "  - name: Authentication\n"
        "    href: authentication.md\n"
        "  - name: Security index\n"
        "    href: index.md\n",
    )
    _write(
        repo,
        EXPECTED_H_EN_NAV,
        "items:\n  - name: Authentication\n    href: authentication.md\n",
    )
    h0 = _commit(repo, "source base before PR 40385")

    h_bodies = {
        H_SEEDS[0]: (
            "# Client certificate authorization\n\n"
            "AUTHORITY-H client-certificate prose.\n\n"
            "{% include [prerequisite](../../security/_assets/"
            "client-certificate-prerequisite.md) %}\n\n"
            "[Shared prerequisite](../../security/_assets/"
            "client-certificate-prerequisite.md#client-certificate-prerequisite)\n"
        ),
        H_SEEDS[1]: (
            "# Monitoring configuration\n\n"
            "AUTHORITY-H monitoring prose.\n\n"
            "[TLS settings](tls.md#tls-settings)\n"
        ),
        H_SEEDS[2]: (
            "# TLS settings {#tls-settings}\n\n"
            "AUTHORITY-H TLS prose.\n\n"
            "[Authentication](../../security/authentication.md#historic-auth)\n"
        ),
        H_SEEDS[3]: ("# Authentication {#historic-auth}\n\nAUTHORITY-H authentication prose.\n"),
        H_SEEDS[4]: (
            "# Security\n\n"
            "AUTHORITY-H security index prose.\n\n"
            "[Authentication](authentication.md#historic-auth)\n"
        ),
    }
    for path, text in h_bodies.items():
        _write(repo, path, text)
    h = _commit(repo, "Merge pull request #40385: historical security docs")
    _git(repo, "branch", "source-40385", h)

    later_bodies = {
        L_ADDED[0]: (
            "# Caching authentication results {#token-cache}\n\n"
            "LATER-L cache result semantics and invalidation rules.\n\n"
            "{% include [token](_assets/user-token.md) %}\n\n"
            "{% include [lifecycle](_assets/user-token-lifecycle.md) %}\n\n"
            "[User token](../concepts/glossary.md#user-token)\n"
        ),
        L_ADDED[1]: ("# User token {#user-token}\n\nLATER-L user token acquisition details.\n"),
        L_ADDED[2]: (
            "# User token lifecycle {#user-token-lifecycle}\n\n"
            "LATER-L token refresh and revocation lifecycle.\n"
        ),
        L_MODIFIED[0]: (
            "# Authentication {#historic-auth}\n\n"
            "LATER-L SAME-PATH authentication prose contamination.\n\n"
            "[Cache results](caching-authentication-results.md#token-cache)\n\n"
            "[Authorization](authorization.md)\n"
        ),
        L_MODIFIED[1]: (
            "# Authentication configuration\n\n"
            "LATER-L auth config behavior.\n\n"
            "[Cache results](../../security/caching-authentication-results.md#token-cache)\n"
        ),
        L_MODIFIED[2]: (
            "# Glossary\n\n"
            "## User token {#user-token}\n\n"
            "LATER-L glossary definition for cached credentials.\n"
        ),
        L_MODIFIED[3]: "# Authorization\n\nLATER-L authorization cache interaction.\n",
        L_MODIFIED[4]: (
            "items:\n"
            "  - name: Authentication\n"
            "    href: authentication.md\n"
            "  - name: Caching authentication results\n"
            "    href: caching-authentication-results.md\n"
            "  - name: Security index\n"
            "    href: index.md\n"
        ),
    }
    for path, text in later_bodies.items():
        _write(repo, path, text)
    later = _commit(repo, "Merge pull request #50704: cache authentication results")

    # Independent post-#50704 B drift. It is an authority trap, never part of
    # the #50704 additions/modifications or their provenance report.
    _write(
        repo,
        H_DEPENDENCY,
        "# Shared client certificate prerequisite {#wrong-b-anchor}\n\n"
        "B-DEPENDENCY-TRAP must never become source-preserving RU.\n",
    )
    _write(
        repo,
        B_ARTIFACT,
        json.dumps({"source_pr": LATER_PR, "kind": "generated artifact"}) + "\n",
    )
    baseline = _commit(repo, "independent B-only authority trap and automation artifact")

    _git(repo, "checkout", "-b", "future-main", baseline)
    _write(
        repo,
        H_SEEDS[3],
        (later_bodies[H_SEEDS[3]] + "\nB1-MOVING-MAIN-RU body drift.\n"),
    )
    _write(repo, B1_ONLY_PATH, "# B1 only edge\n\nB1-MOVING-MAIN-RU target.\n")
    _write(
        repo,
        H_NAV,
        later_bodies[H_NAV] + "  - name: B1 moving-main edge\n" + "    href: b1-only-edge.md\n",
    )
    b1 = _commit(repo, "B1 moving-main RU body and navigation edge drift")
    _git(repo, "branch", "-f", "main", baseline)

    _git(repo, "init", "--bare", str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    _git(repo, "push", "origin", f"{baseline}:refs/heads/main")
    _git(repo, "push", "origin", f"{h}:refs/heads/source-40385")
    _git(repo, "fetch", "origin")
    _git(repo, "checkout", "--detach", h)

    return AuthorityHistory(repo, upstream, h0, h, later, baseline, b1)


def _pull(
    *,
    number: int,
    head_ref: str,
    head_sha: str,
    base_sha: str,
    merged: bool,
    merge_sha: str | None,
    body: str = "",
    head_repo: str = REPO_ID,
) -> dict[str, Any]:
    head_owner, head_name = head_repo.split("/", 1)
    return {
        "number": number,
        "title": f"docs PR #{number}",
        "body": body,
        "merged": merged,
        "state": "closed" if merged else "open",
        "merge_commit_sha": merge_sha,
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {
                "clone_url": f"https://github.com/{head_repo}.git",
                "full_name": head_repo,
                "owner": {"login": head_owner},
                "name": head_name,
            },
        },
        "base": {"ref": "main", "sha": base_sha},
    }


@dataclass
class ExactGitHub:
    history: AuthorityHistory
    translation_body: str = ""
    remote_heads: dict[str, str | None] = field(default_factory=dict)
    comments: list[tuple[str, int, str]] = field(default_factory=list)
    mutations: list[tuple[str, Any]] = field(default_factory=list)
    file_calls: list[tuple[str, str, str]] = field(default_factory=list)
    body_reads: list[str] = field(default_factory=list)
    missing_file_keys: set[tuple[str, str, str]] = field(default_factory=set)
    pull_overrides: dict[int, dict[str, Any]] = field(default_factory=dict)
    body_events: list[tuple[str, str | None, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.remote_heads.setdefault(f"ydbdoc-review/pr-{SOURCE_PR}", self.history.candidate)
        self._file_responses: dict[tuple[str, str, str], str | None] = {}
        refs = (
            self.history.h0,
            self.history.h,
            self.history.later,
            self.history.baseline,
            self.history.b1,
        )
        known_paths = set(H_SEEDS) | set(L_PATHS) | {H_DEPENDENCY, H_NAV, B1_ONLY_PATH}
        for ref in refs:
            for path in known_paths:
                self._file_responses[(REPO_ID, ref, path)] = _text_at(self.history.repo, ref, path)

        self._pull_files: dict[int, tuple[tuple[str, str], ...]] = {
            SOURCE_PR: tuple((path, "modified") for path in H_SEEDS),
            LATER_PR: tuple(
                [(path, "added") for path in L_ADDED] + [(path, "modified") for path in L_MODIFIED]
            ),
            OPEN_PR: tuple((path, "modified") for path in H_SEEDS),
            BILINGUAL_PR: (
                (H_SEEDS[3], "modified"),
                (H_SEEDS[3].replace("/ru/", "/en/", 1), "modified"),
            ),
        }

    def _source_pull(self, number: int) -> dict[str, Any]:
        if number in self.pull_overrides:
            return self.pull_overrides[number]
        if number == SOURCE_PR:
            return _pull(
                number=number,
                head_ref="source-40385",
                head_sha=self.history.h,
                base_sha=self.history.h0,
                merged=True,
                merge_sha=self.history.h,
            )
        if number == LATER_PR:
            return _pull(
                number=number,
                head_ref="source-50704",
                head_sha=self.history.later,
                base_sha=self.history.h,
                merged=True,
                merge_sha=self.history.later,
            )
        if number == OPEN_PR:
            return _pull(
                number=number,
                head_ref="source-40385-open-control",
                head_sha=self.history.h,
                base_sha=self.history.h0,
                merged=False,
                merge_sha=None,
            )
        if number == BILINGUAL_PR:
            assert self.history.candidate is not None
            return _pull(
                number=number,
                head_ref="author-bilingual",
                head_sha=self.history.candidate,
                base_sha=self.history.baseline,
                merged=False,
                merge_sha=None,
            )
        raise AssertionError(f"unexpected GitHub pull lookup: {REPO_ID}#{number}")

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        assert f"{owner}/{repo}" == REPO_ID
        if number != TRANSLATION_PR:
            return self._source_pull(number)
        head_sha = self.remote_heads[f"ydbdoc-review/pr-{SOURCE_PR}"]
        assert head_sha is not None, "translation PR requested before candidate publication"
        self.body_reads.append(self.translation_body)
        return _pull(
            number=number,
            head_ref=f"ydbdoc-review/pr-{SOURCE_PR}",
            head_sha=head_sha,
            base_sha=self.history.baseline,
            merged=False,
            merge_sha=None,
            body=self.translation_body,
        )

    def iter_pull_files(self, owner: str, repo: str, number: int) -> Iterator[dict[str, str]]:
        assert f"{owner}/{repo}" == REPO_ID
        if number == TRANSLATION_PR:
            head = self.remote_heads[f"ydbdoc-review/pr-{SOURCE_PR}"]
            assert head is not None
            out = _git_bare(
                self.history.upstream,
                "diff",
                "--name-status",
                self.history.baseline,
                head,
            )
            rows = tuple(tuple(line.split("\t", 1)) for line in out.splitlines())
            mapped = []
            for status, path in rows:
                kind = "added" if status == "A" else "removed" if status == "D" else "modified"
                mapped.append((path, kind))
            rows_for_api = tuple(mapped)
        else:
            if number not in self._pull_files:
                raise AssertionError(f"unexpected GitHub files lookup: {REPO_ID}#{number}")
            rows_for_api = self._pull_files[number]
        yield from ({"filename": path, "status": status} for path, status in rows_for_api)

    def get_file_text(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        key = (f"{owner}/{repo}", ref, path.replace("\\", "/"))
        self.file_calls.append(key)
        if key in self.missing_file_keys:
            return None
        if key not in self._file_responses:
            raise AssertionError(f"unexpected GitHub file lookup {key!r}")
        return self._file_responses[key]

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str | None:
        assert f"{owner}/{repo}" == REPO_ID
        if branch not in self.remote_heads:
            self.remote_heads[branch] = None
        return self.remote_heads[branch]

    def find_open_pull_by_head(
        self, owner: str, repo: str, *, head_branch: str, base: str
    ) -> tuple[str, int] | None:
        assert f"{owner}/{repo}" == REPO_ID
        assert base == "main"
        return None

    def create_pull(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool = False,
    ) -> tuple[str, int, bool]:
        assert f"{owner}/{repo}" == REPO_ID
        assert head == f"ydbdoc-review/pr-{SOURCE_PR}"
        assert base == "main"
        assert self.remote_heads[head] is not None
        self.translation_body = body
        self.body_events.append(("create", self.remote_heads[head], body))
        self.mutations.append(("create_pull", title, head, base, draft))
        return (f"https://github.com/{REPO_ID}/pull/{TRANSLATION_PR}", TRANSLATION_PR, True)

    def update_pull_body(self, owner: str, repo: str, number: int, body: str) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == TRANSLATION_PR
        self.translation_body = body
        branch = f"ydbdoc-review/pr-{SOURCE_PR}"
        self.body_events.append(("update", self.remote_heads[branch], body))
        self.mutations.append(("update_pull_body", number))

    def post_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        assert f"{owner}/{repo}" == REPO_ID
        self.comments.append((REPO_ID, number, body))
        self.mutations.append(("post_issue_comment", number))
        return f"https://github.com/{REPO_ID}/issues/{number}#a05"

    def iter_issue_comments(self, owner: str, repo: str, number: int) -> Iterator[dict[str, Any]]:
        assert f"{owner}/{repo}" == REPO_ID
        return iter(())

    def convert_pull_to_draft(self, owner: str, repo: str, number: int) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        self.mutations.append(("convert_pull_to_draft", number))

    def add_issue_labels(self, owner: str, repo: str, number: int, labels: list[str]) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        self.mutations.append(("add_issue_labels", number, tuple(labels)))

    def delete_branch(self, owner: str, repo: str, branch: str) -> bool:
        assert f"{owner}/{repo}" == REPO_ID
        self.mutations.append(("delete_branch", branch))
        return False


@dataclass
class ObservedRun:
    plans: list[Any] = field(default_factory=list)
    translate_contents: list[list[PairContent]] = field(default_factory=list)
    verify_contents: list[list[PairContent]] = field(default_factory=list)
    label_requests: list[tuple[str, ...]] = field(default_factory=list)
    pushed_shas: list[str] = field(default_factory=list)
    prepare_calls: list[str] = field(default_factory=list)
    declaration_refs: list[str | None] = field(default_factory=list)
    final_qa_paths: list[frozenset[str]] = field(default_factory=list)


def _english_from_ru(text: str, *, keep_broken_dependency: bool) -> str:
    translated = (
        text.replace("AUTHORITY-H", "TRANSLATED-FROM-H")
        .replace("H0 ", "EN H0 ")
        .replace("# ", "# EN ")
    )
    if not keep_broken_dependency:
        translated = re.sub(
            r"\n\[Historical dependency blocker\]\([^\n]+\)\n?",
            "\nHistorical dependency retained without an outbound link.\n",
            translated,
        )
    return translated


def _translation_result(
    contents: list[PairContent],
    *,
    keep_broken_dependency: bool,
    en_fit_ru_override: tuple[str, str] | None = None,
) -> PRTranslationResult:
    runs: list[PairRunResult] = []
    for content in contents:
        plan = plan_pair_heuristic(content)
        assert plan.action == "translate_to_en"
        target = _english_from_ru(
            content.ru_text or "", keep_broken_dependency=keep_broken_dependency
        )
        if en_fit_ru_override is not None and content.pair.ru_path == en_fit_ru_override[0]:
            target = _english_from_ru(
                en_fit_ru_override[1], keep_broken_dependency=keep_broken_dependency
            )
        if content.pair.ru_path == H_DEPENDENCY:
            if "H-DEPENDENCY-SOURCE" in (content.ru_text or ""):
                target = target.replace(" {#client-certificate-prerequisite}", "")
            elif not keep_broken_dependency:
                target = target.replace(" {#wrong-b-anchor}", " {#client-certificate-prerequisite}")
        fr = FileTranslationResult(
            file_path=plan.target_path,
            final_text=target,
            segments_count=1,
            verdict="ok",
            prompt_version="a05-real-entrypoint",
        )
        runs.append(
            PairRunResult(
                plan=plan,
                target_text=target,
                file_result=fr,
                source_text=content.ru_text,
            )
        )
    return PRTranslationResult(pair_results=runs)


def _verify_result(contents: list[PairContent], *, change_one: bool) -> PRTranslationResult:
    runs: list[PairRunResult] = []
    changed = False
    for content in contents:
        pair = content.pair
        plan = PairPlan(
            pair=pair,
            action="critic_only",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="A05 critic boundary",
        )
        target = content.en_text or ""
        if change_one and not changed and content.pair.ru_path != L_MODIFIED[2]:
            target = target.rstrip("\n") + "\n\nA05 verify recursion marker.\n"
            changed = True
        fr = FileTranslationResult(
            file_path=pair.en_path,
            final_text=target,
            segments_count=1,
            verdict="ok",
            prompt_version="a05-real-entrypoint",
        )
        runs.append(
            PairRunResult(
                plan=plan,
                target_text=target,
                file_result=fr,
                source_text=content.ru_text,
            )
        )
    return PRTranslationResult(pair_results=runs)


@contextmanager
def _runtime(
    history: AuthorityHistory,
    gh: ExactGitHub,
    observed: ObservedRun,
    *,
    keep_broken_dependency: bool,
    move_refs_in_translate_model: bool,
    recurse_verify_once: bool,
    patch_inline_verify: bool = False,
    declaration_ref_override: str | None = None,
    en_fit_ru_override: tuple[str, str] | None = None,
) -> Iterator[None]:
    class _BoundaryClient:
        def __init__(self) -> None:
            self.usage_tracker = UsageTracker()
            self.transcript_recorder = None

        def model_chain_for_role(self, role: str) -> list[str]:
            assert role == "translate"
            return ["a05-fixture-translate"]

        def chat(self, messages: list[dict[str, str]], *, role: str) -> SimpleNamespace:
            assert role == "translate"
            request = json.loads(messages[-1]["content"])
            labels = tuple(str(label) for label in request["labels"])
            observed.label_requests.append(labels)
            return SimpleNamespace(
                content=json.dumps(
                    {"translations": [{"ru": label, "en": f"EN {label}"} for label in labels]}
                )
            )

    client = _BoundaryClient()
    real_plan = workflow.plan_translation_scope
    real_prepare = workflow.prepare_translation_branch_on_base
    real_declare = workflow._declare_exact_ascii_fragment_targets_after_apply
    real_final_links = workflow.apply_en_link_target_checks

    def capture_plan(*args: object, **kwargs: object):
        result = real_plan(*args, **kwargs)
        observed.plans.append(result)
        return result

    def translate_model(contents: list[PairContent], *_args: object, **_kwargs: object):
        observed.translate_contents.append(list(contents))
        if move_refs_in_translate_model:
            history.move_named_refs_after_freeze()
        return _translation_result(
            contents,
            keep_broken_dependency=keep_broken_dependency,
            en_fit_ru_override=en_fit_ru_override,
        )

    def verify_model(contents: list[PairContent], *_args: object, **_kwargs: object):
        observed.verify_contents.append(list(contents))
        return _verify_result(
            contents,
            change_one=recurse_verify_once and len(observed.verify_contents) == 1,
        )

    def prepare(repo_path: str, **kwargs: object) -> None:
        observed.prepare_calls.append(str(kwargs["base_commit_sha"]))
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
        assert source_sha is not None
        resolved = _git(Path(repo_path), "rev-parse", f"{source_sha}^{{commit}}")
        actual_before = gh.remote_heads.get(branch)
        assert actual_before == expected_remote_sha
        status = (
            git_ops.RefMutationStatus.NOOP
            if actual_before == resolved
            else git_ops.RefMutationStatus.CHANGED
        )
        _git(
            Path(repo_path),
            "push",
            "--force",
            str(history.upstream),
            f"{resolved}:refs/heads/{branch}",
        )
        gh.remote_heads[branch] = resolved
        history.candidate = resolved
        observed.pushed_shas.append(resolved)
        return git_ops.RefMutationReceipt(
            lease=git_ops.RemoteRefLease(branch=branch, expected_sha=expected_remote_sha),
            operation=git_ops.RefMutationOperation.UPDATE,
            requested_sha=resolved,
            status=status,
            porcelain_flag="+" if status is git_ops.RefMutationStatus.CHANGED else "=",
            stdout="local A05 bare-remote push",
            stderr="",
        )

    def no_orphans(*_args: object, **_kwargs: object) -> list[str]:
        return []

    def capture_declaration(*args: object, **kwargs: object):
        forwarded = dict(kwargs)
        if declaration_ref_override is not None:
            forwarded["ru_content_ref"] = declaration_ref_override
        ref = forwarded.get("ru_content_ref")
        observed.declaration_refs.append(str(ref) if ref is not None else None)
        return real_declare(*args, **forwarded)

    def capture_final_links(*args: object, **kwargs: object):
        observed.final_qa_paths.append(
            frozenset(str(path) for path in kwargs.get("en_md_paths", set()))
        )
        return real_final_links(*args, **kwargs)

    def inline_verify_stub(**_kwargs: object):
        return workflow.DocJobResult(
            mode="doc_verify",
            pr_number=TRANSLATION_PR,
            source_pr_number=SOURCE_PR,
            dry_run=False,
            pr_result=PRTranslationResult(),
        )

    with ExitStack() as stack:
        stack.enter_context(patch.object(workflow, "GitHubClient", return_value=gh))
        stack.enter_context(
            patch.object(
                workflow,
                "begin_ops_job",
                return_value=(None, GateResult(ok=True), None),
            )
        )
        stack.enter_context(patch.object(workflow, "create_llm_client", return_value=client))
        stack.enter_context(
            patch.object(workflow, "load_glossary", return_value=Glossary(entries=[]))
        )
        stack.enter_context(
            patch.object(workflow, "plan_translation_scope", side_effect=capture_plan)
        )
        stack.enter_context(
            patch.object(workflow, "run_pr_translation", side_effect=translate_model)
        )
        stack.enter_context(patch.object(workflow, "_run_verify_pairs", side_effect=verify_model))
        stack.enter_context(
            patch.object(workflow, "prepare_translation_branch_on_base", side_effect=prepare)
        )
        stack.enter_context(patch.object(workflow, "push_branch", side_effect=local_push))
        stack.enter_context(
            patch.object(workflow, "apply_orphan_toc_page_checks", side_effect=no_orphans)
        )
        stack.enter_context(
            patch.object(
                workflow,
                "_declare_exact_ascii_fragment_targets_after_apply",
                side_effect=capture_declaration,
            )
        )
        stack.enter_context(
            patch.object(
                workflow,
                "apply_en_link_target_checks",
                side_effect=capture_final_links,
            )
        )
        if patch_inline_verify:
            stack.enter_context(
                patch.object(workflow, "run_doc_verify", side_effect=inline_verify_stub)
            )
        yield


@dataclass
class CandidateRun:
    history: AuthorityHistory
    gh: ExactGitHub
    observed: ObservedRun
    source_report: str
    job: workflow.DocJobResult


@contextmanager
def _authority_boundary_adapter(
    history: AuthorityHistory,
    *,
    mode: str,
    serialize: bool = False,
) -> Iterator[None]:
    """Isolated downstream adapter, never an entrypoint acceptance oracle.

    Production has no authority-mode input yet. This adapter lets downstream
    lifecycle and mutation tests exercise a coherent R without claiming that
    the public selector exists. Acceptance tests below never enter it.
    """
    assert mode in {"current", "source-preserving"}
    ref = history.baseline if mode == "current" else history.h
    real_loader = workflow.load_pair_contents
    real_body_builder = workflow.build_translation_pr_body

    def coherent_loader(repo_path: str, pairs: list[DocPair], **kwargs: object):
        loaded = real_loader(repo_path, pairs, **kwargs)
        return [
            replace(
                content,
                ru_text=_text_at(history.repo, ref, content.pair.ru_path),
            )
            for content in loaded
        ]

    def producer_body(*args: object, **kwargs: object) -> str:
        body = real_body_builder(*args, **kwargs)
        assert history.candidate is not None
        payload = {
            "version": 1,
            "source": {
                "repo": REPO_ID,
                "pr": SOURCE_PR,
                "head_sha": history.h,
                "base_sha": history.h0,
            },
            "selection": {
                "kind": mode,
                "ru_sha": ref,
                "baseline_sha": history.baseline,
            },
            "candidate_sha": history.candidate,
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
        return body.rstrip() + f"\n\n<!-- {AUTHORITY_WIRE_MARKER}:{encoded} -->\n"

    with ExitStack() as stack:
        # Only the isolated current-mode adapter needs to neutralize the legacy
        # merged-PR resolver. No acceptance test patches this symbol.
        if mode == "current":
            stack.enter_context(
                patch.object(workflow, "translate_ru_content_ref", return_value=None)
            )
        stack.enter_context(
            patch.object(workflow, "load_pair_contents", side_effect=coherent_loader)
        )
        if serialize:
            stack.enter_context(
                patch.object(workflow, "build_translation_pr_body", side_effect=producer_body)
            )
        yield


@contextmanager
def _translate_mutation(history: AuthorityHistory, mutation: str) -> Iterator[None]:
    """One reversible defect injected after the healthy authority adapter."""
    real_loader = workflow.load_pair_contents
    with ExitStack() as stack:
        if mutation in {"always-H", "always-B", "planner-loader-split"}:
            wrong_ref = history.h if mutation == "always-H" else history.baseline

            def wrong_loader(repo_path: str, pairs: list[DocPair], **kwargs: object):
                loaded = real_loader(repo_path, pairs, **kwargs)
                return [
                    replace(
                        content,
                        ru_text=_text_at(history.repo, wrong_ref, content.pair.ru_path),
                    )
                    for content in loaded
                ]

            stack.enter_context(
                patch.object(workflow, "load_pair_contents", side_effect=wrong_loader)
            )
        elif mutation == "H0-to-B":

            def expanded_range(*_args: object, **_kwargs: object):
                rows = []
                for status, path in _name_status(history.repo, history.h0, history.baseline):
                    rows.append(
                        (
                            path,
                            "added"
                            if status == "A"
                            else "deleted"
                            if status == "D"
                            else "modified",
                        )
                    )
                return rows

            stack.enter_context(
                patch.object(workflow, "list_pr_file_changes_api", side_effect=expanded_range)
            )
        elif mutation == "wrong-nav":
            from ydbdoc_review.pipeline import navigation_merge

            real_read = navigation_merge.read_text_at_commit

            def wrong_nav(repo_path: str, ref: str, path: str):
                if ref == history.h and path == H_NAV:
                    return _text_at(history.repo, history.baseline, H_NAV)
                return real_read(repo_path, ref, path)

            stack.enter_context(
                patch.object(navigation_merge, "read_text_at_commit", side_effect=wrong_nav)
            )
        else:
            raise AssertionError(f"unknown test mutation: {mutation}")
        yield


def _translate_candidate(
    history: AuthorityHistory,
    *,
    keep_broken_dependency: bool,
    move_refs: bool = True,
    adapter_mode: str | None = None,
    serialize_adapter: bool = False,
    en_fit_ref: str | None = None,
    declaration_ref_override: str | None = None,
    config=None,
    github: ExactGitHub | None = None,
) -> CandidateRun:
    _git(history.repo, "checkout", "--detach", history.baseline)
    gh = github or ExactGitHub(history)
    observed = ObservedRun()
    with ExitStack() as stack:
        if adapter_mode is not None:
            stack.enter_context(
                _authority_boundary_adapter(
                    history,
                    mode=adapter_mode,
                    serialize=serialize_adapter,
                )
            )
        stack.enter_context(
            _runtime(
                history,
                gh,
                observed,
                keep_broken_dependency=keep_broken_dependency,
                move_refs_in_translate_model=move_refs,
                recurse_verify_once=False,
                patch_inline_verify=True,
                en_fit_ru_override=(
                    (H_SEEDS[3], _text_at(history.repo, en_fit_ref, H_SEEDS[3]) or "")
                    if en_fit_ref is not None
                    else None
                ),
                declaration_ref_override=declaration_ref_override,
            )
        )
        job = workflow.run_doc_translate(
            repo_path=str(history.repo),
            github_repo=REPO_ID,
            pr_number=SOURCE_PR,
            merge_base_with=history.baseline,
            config=config or _config(ru_authority_mode=adapter_mode),
        )
    assert job.committed and job.pushed
    assert job.translation_pr_number == TRANSLATION_PR
    assert observed.pushed_shas
    history.candidate = observed.pushed_shas[-1]
    reports = [
        body for repo, number, body in gh.comments if repo == REPO_ID and number == SOURCE_PR
    ]
    assert reports
    return CandidateRun(history, gh, observed, reports[-1], job)


def _dry_translate(
    history: AuthorityHistory,
    *,
    config=None,
    adapter_mode: str | None = None,
    mutation: str | None = None,
    github: ExactGitHub | None = None,
) -> tuple[workflow.DocJobResult, ObservedRun]:
    """Run the same merged PR; adapter use is explicit at every callsite."""
    _git(history.repo, "checkout", "--detach", history.baseline)
    gh = github or ExactGitHub(history)
    observed = ObservedRun()
    with ExitStack() as stack:
        if adapter_mode is not None:
            stack.enter_context(_authority_boundary_adapter(history, mode=adapter_mode))
        if mutation is not None:
            stack.enter_context(_translate_mutation(history, mutation))
        stack.enter_context(
            _runtime(
                history,
                gh,
                observed,
                keep_broken_dependency=False,
                move_refs_in_translate_model=False,
                recurse_verify_once=False,
                patch_inline_verify=True,
            )
        )
        job = workflow.run_doc_translate(
            repo_path=str(history.repo),
            github_repo=REPO_ID,
            pr_number=SOURCE_PR,
            merge_base_with=history.baseline,
            dry_run=True,
            config=config or _config(ru_authority_mode=adapter_mode),
        )
    return job, observed


def _contents_by_path(contents: list[PairContent]) -> dict[str, PairContent]:
    return {content.pair.ru_path: content for content in contents}


def _expected_b_scope() -> frozenset[str]:
    return frozenset(
        (
            *H_SEEDS,
            H_DEPENDENCY,
            L_ADDED[0],
            L_ADDED[1],
            L_ADDED[2],
            L_MODIFIED[2],
            L_MODIFIED[3],
        )
    )


def _assert_translate_snapshot(
    job: workflow.DocJobResult,
    observed: ObservedRun,
    history: AuthorityHistory,
    *,
    ref: str,
    expected_docs: frozenset[str],
    expect_later_nav: bool,
) -> None:
    assert job.dry_run
    assert len(observed.plans) == 1
    plan = observed.plans[0]
    assert plan.doc_ru_paths == expected_docs
    assert plan.nav_ru_paths == EXPECTED_H_NAV_SCOPE
    assert len(observed.translate_contents) == 1
    by_path = _contents_by_path(observed.translate_contents[0])
    assert frozenset(by_path) == expected_docs
    for path, content in by_path.items():
        assert content.ru_text == _text_at(history.repo, ref, path)
    assert len(job.pr_result.navigation_results) == 1
    nav = job.pr_result.navigation_results[0]
    assert nav.ru_path == H_NAV
    assert nav.target_text is not None
    assert ("caching-authentication-results.md" in nav.target_text) is expect_later_nav
    if ref == history.h:
        assert not plan.all_ru_paths & L_ONLY_PATHS
        assert "SAME-PATH" not in (by_path[H_SEEDS[3]].ru_text or "")
        assert "H-DEPENDENCY-SOURCE" in (by_path[H_DEPENDENCY].ru_text or "")
        assert "B-DEPENDENCY-TRAP" not in (by_path[H_DEPENDENCY].ru_text or "")
    else:
        assert L_ADDED[0] in plan.doc_from_main
        assert "SAME-PATH" in (by_path[H_SEEDS[3]].ru_text or "")
        assert "B-DEPENDENCY-TRAP" in (by_path[H_DEPENDENCY].ru_text or "")


def _assert_h_model_inputs(contents: list[PairContent], history: AuthorityHistory) -> None:
    by_path = _contents_by_path(contents)
    assert frozenset(by_path) == EXPECTED_H_DOC_SCOPE
    for path, content in by_path.items():
        assert content.ru_text == _text_at(history.repo, history.h, path)
    assert "AUTHORITY-H authentication prose" in (by_path[H_SEEDS[3]].ru_text or "")
    assert "H-DEPENDENCY-SOURCE" in (by_path[H_DEPENDENCY].ru_text or "")
    assert "LATER-L" not in "".join(content.ru_text or "" for content in by_path.values())
    assert "B-DEPENDENCY-TRAP" not in (by_path[H_DEPENDENCY].ru_text or "")


@dataclass(frozen=True)
class _EvidenceEnvelope:
    comment_start: int
    comment_end: int
    encoded: str
    payload: object


def _json_scalars(value: object) -> list[str]:
    if isinstance(value, (str, int)):
        return [str(value)]
    if isinstance(value, dict):
        return [scalar for item in value.values() for scalar in _json_scalars(item)]
    if isinstance(value, list):
        return [scalar for item in value for scalar in _json_scalars(item)]
    return []


def _authority_envelope(body: str, history: AuthorityHistory) -> _EvidenceEnvelope:
    """Producer oracle: discover a self-contained envelope by immutable values."""
    required = {
        REPO_ID,
        str(SOURCE_PR),
        history.h0,
        history.h,
        history.baseline,
        str(history.candidate),
    }
    for comment in re.finditer(r"<!--.*?-->", body, flags=re.DOTALL):
        for match in re.finditer(r"[A-Za-z0-9_-]{16,}", comment.group(0)):
            encoded = match.group(0)
            try:
                raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
                payload = json.loads(raw)
            except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if required.issubset(set(_json_scalars(payload))):
                return _EvidenceEnvelope(comment.start(), comment.end(), encoded, payload)
    raise AssertionError("translation PR body lacks a durable envelope binding repo/PR/H0/H/B/C")


AUTHORITY_WIRE_MARKER = "ydbdoc-ru-authority:v1"


def _literal_evidence_payload(history: AuthorityHistory) -> dict[str, object]:
    """Independent consumer fixture. It is never derived from producer output."""
    assert history.candidate is not None
    return {
        "version": 1,
        "source": {
            "repo": REPO_ID,
            "pr": SOURCE_PR,
            "head_sha": history.h,
            "base_sha": history.h0,
        },
        "selection": {
            "kind": "source-preserving",
            "ru_sha": history.h,
            "baseline_sha": history.baseline,
        },
        "candidate_sha": history.candidate,
    }


def _literal_evidence_body(payload: dict[str, object]) -> str:
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    return (
        f"Historical source-preserving translation.\n\n<!-- {AUTHORITY_WIRE_MARKER}:{encoded} -->\n"
    )


def _manual_candidate(history: AuthorityHistory) -> str:
    """Publish C independently of doc_translate and its serializer."""
    _git(history.repo, "checkout", "--detach", history.baseline)
    for ru_path in EXPECTED_H_DOC_SCOPE:
        en_path = counterpart(ru_path, "ydb/docs")
        assert en_path is not None
        ru_text = _text_at(history.repo, history.h, ru_path)
        assert ru_text is not None
        _write(history.repo, en_path, _english_from_ru(ru_text, keep_broken_dependency=False))
    _write(
        history.repo,
        EXPECTED_H_EN_NAV,
        "items:\n"
        "  - name: EN Authentication\n"
        "    href: authentication.md\n"
        "  - name: EN Security index\n"
        "    href: index.md\n",
    )
    candidate = _commit(history.repo, "manual source-preserving candidate C")
    _git(
        history.repo,
        "push",
        str(history.upstream),
        f"{candidate}:refs/heads/ydbdoc-review/pr-{SOURCE_PR}",
    )
    history.candidate = candidate
    return candidate


def _manual_open_parent_candidate(history: AuthorityHistory) -> str:
    """Publish C with direct parent H, as required for a same-repo open route."""
    _git(history.repo, "checkout", "--detach", history.h)
    for ru_path in EXPECTED_H_DOC_SCOPE:
        en_path = counterpart(ru_path, "ydb/docs")
        assert en_path is not None
        ru_text = _text_at(history.repo, history.h, ru_path)
        assert ru_text is not None
        _write(history.repo, en_path, _english_from_ru(ru_text, keep_broken_dependency=False))
    candidate = _commit(history.repo, "manual same-repo open candidate C")
    _git(
        history.repo,
        "push",
        str(history.upstream),
        f"{candidate}:refs/heads/ydbdoc-review/pr-{SOURCE_PR}",
    )
    history.candidate = candidate
    return candidate


def _clone_consumer(tmp_path: Path, history: AuthorityHistory, *, shallow: bool) -> Path:
    consumer = tmp_path / ("consumer-shallow" if shallow else "consumer")
    command = [
        "git",
        "clone",
        "--branch",
        f"ydbdoc-review/pr-{SOURCE_PR}",
        "--single-branch",
    ]
    if shallow:
        command.extend(("--depth", "2"))
    command.extend((f"file://{history.upstream}", str(consumer)))
    subprocess.run(command, check=True, capture_output=True, text=True)
    _git(consumer, "config", "user.email", "a05-consumer@example.test")
    _git(consumer, "config", "user.name", "A05 Consumer")
    fetch = ["fetch"]
    if shallow:
        fetch.extend(("--depth", "2"))
    fetch.extend(("origin", "main:refs/remotes/origin/main"))
    _git(consumer, *fetch)
    if shallow:
        _git(
            consumer,
            "fetch",
            "--depth",
            "1",
            "origin",
            f"{history.h0}:refs/a05/h0",
        )
        blocked_transport = tmp_path / "blocked-exact-sha-transport.git"
        subprocess.run(
            ["git", "init", "--bare", str(blocked_transport)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(consumer, "remote", "set-url", "origin", str(blocked_transport))
    return consumer


def _can_read(repo: Path, ref: str, path: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{path}"],
            capture_output=True,
        ).returncode
        == 0
    )


def _checkout_fingerprint(repo: Path) -> tuple[str, ...]:
    return (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "ls-files", "-s"),
        _git(repo, "status", "--porcelain=v1"),
        _git(repo, "diff", "--binary"),
        _git(repo, "diff", "--cached", "--binary"),
    )


def _negative_literal_body(history: AuthorityHistory, failure_mode: str) -> str:
    payload = _literal_evidence_payload(history)
    if failure_mode == "missing-evidence":
        return "Historical source-preserving translation without evidence.\n"
    if failure_mode == "malformed-evidence":
        return f"<!-- {AUTHORITY_WIRE_MARKER}:%%% -->\n"
    if failure_mode == "foreign-source-identity":
        source = dict(payload["source"])
        source["repo"] = "foreign-owner/foreign-repo"
        source["pr"] = SOURCE_PR + 1
        payload["source"] = source
    elif failure_mode == "mismatched-R-and-H":
        selection = dict(payload["selection"])
        selection["ru_sha"] = history.later
        payload["selection"] = selection
    return _literal_evidence_body(payload)


def _assert_verify_rejected_before_work(
    tmp_path: Path,
    history: AuthorityHistory,
    *,
    failure_mode: str,
) -> None:
    shallow = failure_mode == "unavailable-H-path"
    consumer = _clone_consumer(tmp_path, history, shallow=shallow)
    body = _negative_literal_body(history, failure_mode)
    gh = ExactGitHub(history, translation_body=body)
    if shallow:
        all_h_paths = {*H_SEEDS, H_DEPENDENCY, H_NAV}
        gh.missing_file_keys.update((REPO_ID, history.h, path) for path in all_h_paths)
        assert _git(consumer, "merge-base", "HEAD", "origin/main") == history.baseline
        assert _can_read(consumer, "refs/a05/h0", H_DEPENDENCY)
        assert _can_read(consumer, history.baseline, H_SEEDS[3])
        assert _can_read(consumer, "HEAD", H_SEEDS[3])
        assert _can_read(consumer, "origin/main", B1_ONLY_PATH)
        assert not any(_can_read(consumer, history.h, path) for path in all_h_paths)
        assert _can_read(consumer, "origin/main", H_SEEDS[3])
        assert "LATER-L SAME-PATH" in (consumer / H_SEEDS[3]).read_text(encoding="utf-8")
        exact_fetch = subprocess.run(
            ["git", "-C", str(consumer), "fetch", "origin", history.h],
            capture_output=True,
            text=True,
        )
        assert exact_fetch.returncode != 0
        assert history.h in exact_fetch.stderr
    observed = ObservedRun()
    before = _checkout_fingerprint(consumer)
    mutations_before = tuple(gh.mutations)
    with _runtime(
        history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=False,
        patch_inline_verify=False,
    ):
        with pytest.raises((RuntimeError, ValueError)) as raised:
            workflow.run_doc_verify(
                repo_path=str(consumer),
                github_repo=REPO_ID,
                pr_number=TRANSLATION_PR,
                merge_base_with="origin/main",
                dry_run=False,
                config=_config(),
                skip_ops_gates=True,
            )
    message = str(raised.value)
    cause_patterns = {
        "missing-evidence": r"(?i)evidence|provenance|authority.*missing",
        "malformed-evidence": r"(?i)malformed|decode|evidence|provenance",
        "foreign-source-identity": r"(?i)repo|pull request|source.*identity|foreign",
        "mismatched-R-and-H": r"(?i)mismatch|ru.sha|head.sha|authority",
        "unavailable-H-path": r"(?i)authority|unavailable|missing",
    }
    assert re.search(cause_patterns[failure_mode], message)
    if shallow:
        assert history.h in message
    assert gh.body_reads == [body]
    assert not observed.translate_contents
    assert not observed.verify_contents
    assert not observed.label_requests
    assert not observed.prepare_calls
    assert not observed.pushed_shas
    assert tuple(gh.mutations) == mutations_before
    assert _checkout_fingerprint(consumer) == before


def _later_report_oracle(report: str, history: AuthorityHistory) -> None:
    assert f"#{LATER_PR}" in report
    assert history.later in report
    for path in L_ADDED:
        assert path in report
        row = next(line for line in report.splitlines() if path in line)
        assert re.search(r"(?i)add|добав", row)
    for path in L_MODIFIED:
        assert path in report
        row = next(line for line in report.splitlines() if path in line)
        assert re.search(r"(?i)modif|измен", row)
    lowered = report.lower()
    for needle in ("cache", "user-token", "glossary", "same-path"):
        assert needle in lowered
    normalized_lines = [line.lower().replace("`", "") for line in report.splitlines()]
    assert any(
        "authentication.md" in line and "caching-authentication-results.md" in line
        for line in normalized_lines
    )
    assert any(
        "auth_config.md" in line and "caching-authentication-results.md" in line
        for line in normalized_lines
    )
    relationship_text = "\n".join(normalized_lines)
    caching_section = relationship_text[
        relationship_text.find("caching-authentication-results.md") :
    ]
    assert "_assets/user-token.md" in caching_section
    assert "_assets/user-token-lifecycle.md" in caching_section
    assert "glossary.md#user-token" in caching_section
    # The independent B-only RU change is relevant drift, but its commit has no
    # PR marker.  It must therefore be reported as commit-only/unknown, never
    # silently omitted or attributed to the preceding #50704 merge commit.
    assert history.baseline in report
    assert H_DEPENDENCY in report
    assert re.search(r"(?i)unknown|неизвест", report)
    pr_section_start = report.index(f"#{LATER_PR}")
    unknown_section_start = min(
        index
        for needle in ("unknown", "неизвест")
        if (index := report.lower().find(needle, pr_section_start)) >= 0
    )
    assert H_DEPENDENCY not in report[pr_section_start:unknown_section_start]
    assert B_ARTIFACT not in report


def test_real_history_has_exact_commits_and_critical_dependency_chain(
    authority_history: AuthorityHistory,
) -> None:
    h = authority_history
    assert len({h.h0, h.h, h.later, h.baseline, h.b1}) == 5
    assert _git(h.repo, "merge-base", "--is-ancestor", h.h0, h.h) == ""
    assert _git(h.repo, "merge-base", "--is-ancestor", h.later, h.baseline) == ""
    assert _name_status(h.repo, h.h0, h.h) == tuple(("M", path) for path in H_SEEDS)
    assert _text_at(h.repo, h.h0, H_DEPENDENCY) == _text_at(h.repo, h.h, H_DEPENDENCY)
    assert _text_at(h.repo, h.h, H_DEPENDENCY) != _text_at(h.repo, h.baseline, H_DEPENDENCY)
    assert "H-DEPENDENCY-SOURCE" in (_text_at(h.repo, h.h, H_DEPENDENCY) or "")
    assert "B-DEPENDENCY-TRAP" in (_text_at(h.repo, h.baseline, H_DEPENDENCY) or "")

    assert frozenset(_name_status(h.repo, h.h, h.later)) == frozenset(
        [("A", path) for path in L_ADDED] + [("M", path) for path in L_MODIFIED]
    )
    assert frozenset(_name_status(h.repo, h.later, h.baseline)) == frozenset(
        (("M", H_DEPENDENCY), ("A", B_ARTIFACT))
    )
    assert frozenset(_name_status(h.repo, h.baseline, h.b1)) == frozenset(
        (("M", H_SEEDS[3]), ("M", H_NAV), ("A", B1_ONLY_PATH))
    )
    assert "B1-MOVING-MAIN-RU" in (_text_at(h.repo, h.b1, H_SEEDS[3]) or "")
    assert "b1-only-edge.md" in (_text_at(h.repo, h.b1, H_NAV) or "")

    auth = _text_at(h.repo, h.later, H_SEEDS[3]) or ""
    auth_config = _text_at(h.repo, h.later, L_MODIFIED[1]) or ""
    caching = _text_at(h.repo, h.later, L_ADDED[0]) or ""
    glossary = _text_at(h.repo, h.later, L_MODIFIED[2]) or ""
    assert "SAME-PATH" in auth
    assert "caching-authentication-results.md#token-cache" in auth
    assert "caching-authentication-results.md#token-cache" in auth_config
    assert "_assets/user-token.md" in caching
    assert "_assets/user-token-lifecycle.md" in caching
    assert "../concepts/glossary.md#user-token" in caching
    assert "{#user-token}" in glossary
    assert "caching-authentication-results.md" in (_text_at(h.repo, h.later, H_NAV) or "")


def test_same_merged_pr_default_current_mode_uses_exact_b_scope_bytes_and_nav(
    authority_history: AuthorityHistory,
) -> None:
    job, observed = _dry_translate(authority_history)
    _assert_translate_snapshot(
        job,
        observed,
        authority_history,
        ref=authority_history.baseline,
        expected_docs=_expected_b_scope(),
        expect_later_nav=True,
    )


def test_same_merged_pr_explicit_source_preserving_mode_uses_exact_h_everywhere(
    authority_history: AuthorityHistory,
) -> None:
    job, observed = _dry_translate(
        authority_history,
        config=_config(ru_authority_mode="source-preserving"),
    )
    _assert_translate_snapshot(
        job,
        observed,
        authority_history,
        ref=authority_history.h,
        expected_docs=EXPECTED_H_DOC_SCOPE,
        expect_later_nav=False,
    )


def test_merged_route_uses_merge_commit_h_and_parent_h0_not_api_head_or_base(
    authority_history: AuthorityHistory,
) -> None:
    gh = ExactGitHub(authority_history)
    unequal = gh._source_pull(SOURCE_PR)
    unequal["head"]["sha"] = authority_history.later
    unequal["base"]["sha"] = authority_history.baseline
    assert unequal["head"]["sha"] != authority_history.h
    assert unequal["base"]["sha"] != authority_history.h0
    gh.pull_overrides[SOURCE_PR] = unequal

    job, observed = _dry_translate(
        authority_history,
        config=_config(ru_authority_mode="source-preserving"),
        github=gh,
    )
    _assert_translate_snapshot(
        job,
        observed,
        authority_history,
        ref=authority_history.h,
        expected_docs=EXPECTED_H_DOC_SCOPE,
        expect_later_nav=False,
    )


def test_configured_authority_mode_changes_real_entrypoint_behavior(
    authority_history: AuthorityHistory,
) -> None:
    """Behavioral interface RED: the config choice must not be silently ignored."""
    default_job, default_observed = _dry_translate(authority_history)
    cleanup_job, cleanup_observed = _dry_translate(
        authority_history,
        config=_config(ru_authority_mode="source-preserving"),
    )

    def signature(job: workflow.DocJobResult, observed: ObservedRun) -> tuple[object, ...]:
        return (
            observed.plans[0].doc_ru_paths,
            tuple(
                (path, content.ru_text)
                for path, content in sorted(
                    _contents_by_path(observed.translate_contents[0]).items()
                )
            ),
            tuple(nav.target_text for nav in job.pr_result.navigation_results),
        )

    assert signature(default_job, default_observed) != signature(cleanup_job, cleanup_observed)


def test_ordinary_open_pr_control_reads_h_from_checkout_via_real_entrypoint(
    authority_history: AuthorityHistory,
) -> None:
    _git(authority_history.repo, "checkout", "--detach", authority_history.h)
    gh = ExactGitHub(authority_history)
    observed = ObservedRun()
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=False,
        patch_inline_verify=True,
    ):
        job = workflow.run_doc_translate(
            repo_path=str(authority_history.repo),
            github_repo=REPO_ID,
            pr_number=OPEN_PR,
            merge_base_with=authority_history.baseline,
            dry_run=True,
            config=_config(),
        )
    _assert_translate_snapshot(
        job,
        observed,
        authority_history,
        ref=authority_history.h,
        expected_docs=EXPECTED_H_DOC_SCOPE,
        expect_later_nav=False,
    )


def test_open_pr_rejects_source_h_different_from_frozen_checkout_s_before_work(
    authority_history: AuthorityHistory,
) -> None:
    _git(authority_history.repo, "checkout", "--detach", authority_history.baseline)
    gh = ExactGitHub(authority_history)
    observed = ObservedRun()
    before = _checkout_fingerprint(authority_history.repo)
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=False,
        patch_inline_verify=True,
    ):
        with pytest.raises(RuntimeError, match=r"(?i)source|authority|checkout|snapshot"):
            workflow.run_doc_translate(
                repo_path=str(authority_history.repo),
                github_repo=REPO_ID,
                pr_number=OPEN_PR,
                merge_base_with=authority_history.baseline,
                dry_run=True,
                config=_config(),
            )

    assert not observed.plans
    assert not observed.translate_contents
    assert not observed.prepare_calls
    assert not observed.pushed_shas
    assert not gh.mutations
    assert _checkout_fingerprint(authority_history.repo) == before


def test_source_preserving_downstream_adapter_runs_real_apply_nav_and_final_qa(
    authority_history: AuthorityHistory,
) -> None:
    run = _translate_candidate(
        authority_history,
        keep_broken_dependency=True,
        move_refs=True,
        adapter_mode="source-preserving",
    )
    _assert_h_model_inputs(run.observed.translate_contents[0], authority_history)
    assert run.observed.label_requests
    assert run.observed.prepare_calls == [authority_history.baseline]
    assert run.observed.declaration_refs == [authority_history.h]
    assert run.observed.final_qa_paths == [EXPECTED_H_EN_WRITES]
    nav = run.job.pr_result.navigation_results[0]
    assert nav.target_text is not None
    assert "caching-authentication-results.md" not in nav.target_text
    assert not nav.error

    candidate = authority_history.candidate
    assert candidate is not None
    assert _git(authority_history.repo, "rev-parse", f"{candidate}^") == authority_history.baseline
    changed = frozenset(
        path
        for _status, path in _name_status(
            authority_history.repo, authority_history.baseline, candidate
        )
    )
    assert changed == EXPECTED_H_EN_WRITES | {EXPECTED_H_EN_NAV}
    assert not changed & {path.replace("/ru/", "/en/", 1) for path in L_ONLY_PATHS}
    dependency_en = H_DEPENDENCY.replace("/ru/", "/en/", 1)
    dependency_text = _text_at(authority_history.repo, candidate, dependency_en) or ""
    assert "H-DEPENDENCY-SOURCE" in dependency_text
    assert "B-DEPENDENCY-TRAP" not in dependency_text
    assert "{#client-certificate-prerequisite}" in dependency_text
    blocker_paths = {blocker.path for blocker in run.job.pr_result.final_tree_blockers}
    assert dependency_en in blocker_paths
    assert not set(run.job.pr_result.completeness_gaps) & {
        path.replace("/ru/", "/en/", 1) for path in L_ONLY_PATHS
    }
    assert not blocker_paths & {path.replace("/ru/", "/en/", 1) for path in L_ONLY_PATHS}


def test_translate_producer_serializes_repo_pr_h0_h_b_and_candidate(
    authority_history: AuthorityHistory,
) -> None:
    run = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
    )
    envelope = _authority_envelope(run.gh.translation_body, authority_history)
    assert isinstance(envelope.payload, dict)
    assert envelope.payload["selection"] == {
        "kind": "current",
        "ru_sha": authority_history.baseline,
        "baseline_sha": authority_history.baseline,
    }
    assert envelope.payload["candidate_sha"] == authority_history.candidate


def test_red_precreated_body_has_no_future_c_then_postconfirm_update_binds_c(
    authority_history: AuthorityHistory,
) -> None:
    old_candidate = _manual_candidate(authority_history)
    gh = ExactGitHub(authority_history)
    run = _translate_candidate(
        authority_history,
        keep_broken_dependency=True,
        move_refs=False,
        config=_config(ru_authority_mode="source-preserving"),
        github=gh,
    )
    candidate = authority_history.candidate
    assert candidate is not None and candidate != old_candidate
    assert [event[0] for event in gh.body_events] == ["create", "update"]
    prepush, postconfirm = gh.body_events
    assert prepush[1] == old_candidate
    assert AUTHORITY_WIRE_MARKER not in prepush[2]
    assert postconfirm[1] == candidate
    assert (
        _authority_envelope(postconfirm[2], authority_history).payload["candidate_sha"] == candidate
    )
    assert run.job.pr_result.publication_impact.value == "PUBLISH_RED"


def test_actual_job_report_names_exact_50704_drift_and_excludes_artifact(
    authority_history: AuthorityHistory,
) -> None:
    run = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
    )
    _later_report_oracle(run.source_report, authority_history)


def test_actual_job_report_oracle_kills_semantic_wrong_drift_report_and_recovers(
    authority_history: AuthorityHistory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real source-comment consumer rejects a misattributed #50704 report."""
    healthy = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
    )
    _later_report_oracle(healthy.source_report, authority_history)

    original = workflow.build_later_ru_drift_report

    def wrong_report(*args: object, **kwargs: object) -> str:
        return original(*args, **kwargs).replace(
            f"Verified PR #{LATER_PR}", f"Verified PR #{LATER_PR + 1}", 1
        )

    with monkeypatch.context() as altered:
        altered.setattr(workflow, "build_later_ru_drift_report", wrong_report)
        mutant = _translate_candidate(
            authority_history,
            keep_broken_dependency=False,
            move_refs=False,
        )
        with pytest.raises(AssertionError):
            _later_report_oracle(mutant.source_report, authority_history)

    recovered = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
    )
    _later_report_oracle(recovered.source_report, authority_history)


def test_drift_subject_pr_number_is_not_attributed_without_exact_api_association(
    authority_history: AuthorityHistory,
) -> None:
    gh = ExactGitHub(authority_history)
    unrelated = gh._source_pull(LATER_PR)
    unrelated["merge_commit_sha"] = authority_history.baseline
    gh.pull_overrides[LATER_PR] = unrelated
    run = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
        github=gh,
    )
    assert f"Verified PR #{LATER_PR}" not in run.source_report
    assert f"unverified subject candidate #{LATER_PR}" in run.source_report
    assert authority_history.later in run.source_report


def test_unadapted_source_preserving_non_dry_producer_and_consumer_roundtrip(
    tmp_path: Path,
    authority_history: AuthorityHistory,
) -> None:
    """Full acceptance path: no authority selection or serializer patch."""
    producer = _translate_candidate(
        authority_history,
        keep_broken_dependency=True,
        move_refs=True,
        config=_config(ru_authority_mode="source-preserving"),
    )
    assert producer.observed.plans[0].doc_ru_paths == EXPECTED_H_DOC_SCOPE
    _assert_h_model_inputs(producer.observed.translate_contents[0], authority_history)
    assert producer.observed.declaration_refs == [authority_history.h]
    candidate = authority_history.candidate
    assert candidate is not None

    body = producer.gh.translation_body
    envelope = _authority_envelope(body, authority_history)
    assert envelope.payload == {
        "version": 1,
        "source": {
            "repo": REPO_ID,
            "pr": SOURCE_PR,
            "head_sha": authority_history.h,
            "base_sha": authority_history.h0,
        },
        "selection": {
            "kind": "source-preserving",
            "ru_sha": authority_history.h,
            "baseline_sha": authority_history.baseline,
        },
        "candidate_sha": candidate,
    }
    _later_report_oracle(producer.source_report, authority_history)

    consumer = _clone_consumer(tmp_path, authority_history, shallow=False)
    gh = ExactGitHub(authority_history, translation_body=body)
    observed = ObservedRun()
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=False,
        patch_inline_verify=False,
    ):
        job = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            dry_run=True,
            config=_config(),
            skip_ops_gates=True,
        )
    assert job.dry_run
    assert gh.body_reads == [body]
    assert len(observed.verify_contents) == 1
    _assert_h_model_inputs(observed.verify_contents[0], authority_history)


def test_open_origin_current_evidence_keeps_r_h_after_source_later_merges(
    tmp_path: Path,
    authority_history: AuthorityHistory,
) -> None:
    candidate = _manual_open_parent_candidate(authority_history)
    payload = _literal_evidence_payload(authority_history)
    selection = dict(payload["selection"])
    selection["kind"] = "current"
    payload["selection"] = selection
    body = _literal_evidence_body(payload)

    consumer = _clone_consumer(tmp_path, authority_history, shallow=False)
    gh = ExactGitHub(authority_history, translation_body=body)
    later_state = gh._source_pull(SOURCE_PR)
    later_state["head"]["sha"] = authority_history.later
    later_state["base"]["sha"] = authority_history.baseline
    assert later_state["merged"] is True
    gh.pull_overrides[SOURCE_PR] = later_state
    observed = ObservedRun()
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=False,
        patch_inline_verify=False,
    ):
        job = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            dry_run=True,
            config=_config(),
            skip_ops_gates=True,
        )
    assert job.dry_run
    assert _git(consumer, "rev-parse", "HEAD") == candidate
    assert len(observed.verify_contents) == 1
    _assert_h_model_inputs(observed.verify_contents[0], authority_history)


def test_reuse_preserves_old_c_r_and_rejects_branch_rebound_to_new_sha(
    authority_history: AuthorityHistory,
) -> None:
    candidate = _manual_candidate(authority_history)
    blocker = workflow.FinalTreeBlocker(
        path=H_DEPENDENCY.replace("/ru/", "/en/", 1),
        code="translation_soft_keep",
        message="retained exact artifact",
        artifact_sha256="1" * 64,
    )
    result = PRTranslationResult(final_tree_blockers=[blocker])
    provenance = TranslationArtifactProvenance(
        RuAuthority(
            source_repo=REPO_ID,
            source_pr=SOURCE_PR,
            source_base_sha=authority_history.h0,
            source_head_sha=authority_history.h,
            baseline_sha=authority_history.baseline,
            ru_sha=authority_history.h,
            mode=RuAuthorityMode.SOURCE_PRESERVING,
        ),
        candidate,
    )
    body = workflow.build_translation_pr_body(
        SOURCE_PR,
        REPO_ID,
        publication_result=result,
        provenance=provenance,
    )
    gh = ExactGitHub(authority_history, translation_body=body)
    gh.find_open_pull_by_head = lambda *_args, **_kwargs: (  # type: ignore[method-assign]
        f"https://github.com/{REPO_ID}/pull/{TRANSLATION_PR}",
        TRANSLATION_PR,
    )
    branch = f"ydbdoc-review/pr-{SOURCE_PR}"
    bound = workflow._matching_existing_soft_keep_artifact_pr(
        gh,
        *REPO_ID.split("/", 1),
        branch=branch,
        base="main",
        result=result,
        expected_remote_sha=candidate,
        repo_path=str(authority_history.repo),
        source_repo=REPO_ID,
        source_pr=SOURCE_PR,
    )
    assert bound is not None
    assert bound.provenance.candidate_sha == candidate
    assert bound.provenance.authority.ru_sha == authority_history.h
    assert bound.provenance.authority.baseline_sha == authority_history.baseline

    refreshed = workflow.build_translation_pr_body(
        SOURCE_PR,
        REPO_ID,
        publication_result=PRTranslationResult(),
        provenance=bound.provenance,
    )
    assert (
        _authority_envelope(refreshed, authority_history).payload
        == _authority_envelope(body, authority_history).payload
    )

    gh.remote_heads[branch] = authority_history.b1
    assert (
        workflow._matching_existing_soft_keep_artifact_pr(
            gh,
            *REPO_ID.split("/", 1),
            branch=branch,
            base="main",
            result=result,
            expected_remote_sha=authority_history.b1,
            repo_path=str(authority_history.repo),
            source_repo=REPO_ID,
            source_pr=SOURCE_PR,
        )
        is None
    )


def test_producer_body_roundtrip_downstream_runs_independent_verify_and_one_push(
    tmp_path: Path,
    authority_history: AuthorityHistory,
) -> None:
    producer = _translate_candidate(
        authority_history,
        keep_broken_dependency=True,
        move_refs=True,
        config=_config(ru_authority_mode="source-preserving"),
    )
    candidate = authority_history.candidate
    assert candidate is not None
    body = producer.gh.translation_body
    envelope = _authority_envelope(body, authority_history)
    assert envelope.payload == {
        "version": 1,
        "source": {
            "repo": REPO_ID,
            "pr": SOURCE_PR,
            "head_sha": authority_history.h,
            "base_sha": authority_history.h0,
        },
        "selection": {
            "kind": "source-preserving",
            "ru_sha": authority_history.h,
            "baseline_sha": authority_history.baseline,
        },
        "candidate_sha": candidate,
    }
    consumer = _clone_consumer(tmp_path, authority_history, shallow=False)
    gh = ExactGitHub(authority_history, translation_body=body)
    assert gh is not producer.gh
    assert producer.observed.declaration_refs == [authority_history.h]
    assert producer.observed.final_qa_paths == [EXPECTED_H_EN_WRITES]
    observed = ObservedRun()
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=True,
        patch_inline_verify=False,
    ):
        job = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_config(),
            skip_ops_gates=True,
        )

    assert gh.body_reads == [body, body]
    assert len(observed.pushed_shas) == 1
    pushed = observed.pushed_shas[0]
    assert _git(consumer, "rev-parse", f"{pushed}^") == candidate
    assert len(observed.verify_contents) == 2
    for contents in observed.verify_contents:
        _assert_h_model_inputs(contents, authority_history)
    first = _contents_by_path(observed.verify_contents[0])
    second = _contents_by_path(observed.verify_contents[1])
    marker_paths = [
        path for path in first if "A05 verify recursion marker" in (second[path].en_text or "")
    ]
    assert len(marker_paths) == 1
    assert "A05 verify recursion marker" not in (first[marker_paths[0]].en_text or "")
    for path, content in second.items():
        assert content.en_text == _text_at(consumer, pushed, content.pair.en_path), path
    assert all(plan.doc_ru_paths == EXPECTED_H_DOC_SCOPE for plan in observed.plans)
    assert observed.final_qa_paths == [EXPECTED_H_EN_WRITES, EXPECTED_H_EN_WRITES]
    forbidden_en = {path.replace("/ru/", "/en/", 1) for path in L_ONLY_PATHS}
    assert not set(job.pr_result.completeness_gaps) & forbidden_en
    assert all(
        "caching-authentication-results.md" not in (nav.target_text or "")
        for nav in job.pr_result.navigation_results
    )
    assert all(not nav.error and not nav.warnings for nav in job.pr_result.navigation_results)
    blocker_paths = {blocker.path for blocker in job.pr_result.final_tree_blockers}
    assert H_DEPENDENCY.replace("/ru/", "/en/", 1) in blocker_paths
    assert not blocker_paths & forbidden_en


@pytest.mark.parametrize(
    "failure_mode",
    ["missing-evidence", "foreign-source-identity"],
)
def test_recursive_verify_rejects_evidence_drop_or_replacement_between_passes(
    tmp_path: Path,
    authority_history: AuthorityHistory,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    """The second pass must parse fresh, valid authority evidence before model work."""
    producer = _translate_candidate(
        authority_history,
        keep_broken_dependency=True,
        move_refs=True,
        config=_config(ru_authority_mode="source-preserving"),
    )
    body = producer.gh.translation_body
    consumer = _clone_consumer(tmp_path, authority_history, shallow=False)
    gh = ExactGitHub(authority_history, translation_body=body)
    original_get_pull = gh.get_pull
    translation_reads = 0

    def get_pull_after_first_pass(owner: str, repo: str, number: int) -> dict[str, Any]:
        nonlocal translation_reads
        pull = original_get_pull(owner, repo, number)
        if number == TRANSLATION_PR:
            translation_reads += 1
            if translation_reads == 2:
                pull["body"] = _negative_literal_body(authority_history, failure_mode)
        return pull

    monkeypatch.setattr(gh, "get_pull", get_pull_after_first_pass)
    observed = ObservedRun()
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=True,
        patch_inline_verify=False,
    ):
        with pytest.raises(ValueError):
            workflow.run_doc_verify(
                repo_path=str(consumer),
                github_repo=REPO_ID,
                pr_number=TRANSLATION_PR,
                merge_base_with="origin/main",
                config=_config(),
                skip_ops_gates=True,
            )

    assert translation_reads == 2
    assert len(observed.verify_contents) == 1
    assert len(observed.pushed_shas) == 1


def test_current_downstream_roundtrip_keeps_b_after_main_moves_and_en_fits_h(
    tmp_path: Path,
    authority_history: AuthorityHistory,
) -> None:
    producer = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=True,
        en_fit_ref=authority_history.h,
    )
    candidate = authority_history.candidate
    assert candidate is not None
    assert authority_history.h != authority_history.baseline
    assert _git(authority_history.repo, "rev-parse", f"{candidate}^") == authority_history.baseline
    assert _git(authority_history.repo, "rev-parse", "refs/heads/main") == authority_history.b1
    assert (
        _git(authority_history.repo, "rev-parse", "refs/heads/source-40385")
        == authority_history.later
    )
    body = producer.gh.translation_body
    envelope = _authority_envelope(body, authority_history)
    assert isinstance(envelope.payload, dict)
    assert envelope.payload["selection"] == {
        "kind": "current",
        "ru_sha": authority_history.baseline,
        "baseline_sha": authority_history.baseline,
    }
    assert envelope.payload["candidate_sha"] == candidate
    auth_en = H_SEEDS[3].replace("/ru/", "/en/", 1)
    candidate_auth_en = _text_at(authority_history.repo, candidate, auth_en)
    assert candidate_auth_en == _english_from_ru(
        _text_at(authority_history.repo, authority_history.h, H_SEEDS[3]) or "",
        keep_broken_dependency=False,
    )
    assert candidate_auth_en != _english_from_ru(
        _text_at(authority_history.repo, authority_history.baseline, H_SEEDS[3]) or "",
        keep_broken_dependency=False,
    )

    consumer = _clone_consumer(tmp_path, authority_history, shallow=False)
    gh = ExactGitHub(authority_history, translation_body=body)
    observed = ObservedRun()
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=True,
        patch_inline_verify=False,
    ):
        job = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_config(),
            skip_ops_gates=True,
        )
    assert not job.dry_run
    assert gh.body_reads == [body, body]
    assert len(observed.pushed_shas) == 1
    pushed = observed.pushed_shas[0]
    assert _git(consumer, "rev-parse", f"{pushed}^") == candidate
    assert len(observed.verify_contents) == 2
    for contents in observed.verify_contents:
        by_path = _contents_by_path(contents)
        assert frozenset(by_path) == _expected_b_scope()
        for path, content in by_path.items():
            assert content.ru_text == _text_at(
                authority_history.repo, authority_history.baseline, path
            )
            assert "B1-MOVING-MAIN-RU" not in (content.ru_text or "")
        assert B1_ONLY_PATH not in by_path
        assert "LATER-L SAME-PATH" in (by_path[H_SEEDS[3]].ru_text or "")
        assert "B-DEPENDENCY-TRAP" in (by_path[H_DEPENDENCY].ru_text or "")

    expected_b_en = frozenset(
        path
        for ru_path in _expected_b_scope()
        if (path := counterpart(ru_path, "ydb/docs")) is not None
    )
    assert observed.final_qa_paths == [expected_b_en, expected_b_en]
    b1_en = B1_ONLY_PATH.replace("/ru/", "/en/", 1)
    assert b1_en not in job.pr_result.completeness_gaps
    assert b1_en not in {blocker.path for blocker in job.pr_result.final_tree_blockers}

    first = _contents_by_path(observed.verify_contents[0])
    second = _contents_by_path(observed.verify_contents[1])
    marker_paths = [
        path for path in first if "A05 verify recursion marker" in (second[path].en_text or "")
    ]
    assert len(marker_paths) == 1
    assert "A05 verify recursion marker" not in (first[marker_paths[0]].en_text or "")
    for path, content in second.items():
        assert content.en_text == _text_at(consumer, pushed, content.pair.en_path), path
    assert all(plan.doc_ru_paths == _expected_b_scope() for plan in observed.plans)
    assert all(
        "b1-only-edge.md" not in (nav.target_text or "") for nav in job.pr_result.navigation_results
    )
    assert _git(consumer, "rev-parse", "origin/main") == authority_history.b1


@pytest.mark.parametrize(
    "failure_mode",
    [
        "missing-evidence",
        "malformed-evidence",
        "foreign-source-identity",
        "mismatched-R-and-H",
        "unavailable-H-path",
    ],
)
def test_literal_consumer_evidence_rejects_each_cause_before_work(
    tmp_path: Path,
    authority_history: AuthorityHistory,
    failure_mode: str,
) -> None:
    _manual_candidate(authority_history)
    authority_history.move_named_refs_after_freeze()
    _assert_verify_rejected_before_work(
        tmp_path,
        authority_history,
        failure_mode=failure_mode,
    )


def test_bilingual_verify_control_reads_both_locales_from_candidate_c(
    authority_history: AuthorityHistory,
) -> None:
    candidate = _manual_candidate(authority_history)
    _git(authority_history.repo, "checkout", "--detach", candidate)
    gh = ExactGitHub(authority_history)
    observed = ObservedRun()
    with _runtime(
        authority_history,
        gh,
        observed,
        keep_broken_dependency=False,
        move_refs_in_translate_model=False,
        recurse_verify_once=False,
        patch_inline_verify=False,
    ):
        job = workflow.run_doc_verify(
            repo_path=str(authority_history.repo),
            github_repo=REPO_ID,
            pr_number=BILINGUAL_PR,
            merge_base_with=authority_history.baseline,
            dry_run=True,
            config=_config(),
            skip_ops_gates=True,
        )
    assert job.dry_run
    auth = _contents_by_path(observed.verify_contents[0])[H_SEEDS[3]]
    assert auth.ru_text == _text_at(authority_history.repo, candidate, H_SEEDS[3])
    assert auth.en_text == _text_at(
        authority_history.repo, candidate, H_SEEDS[3].replace("/ru/", "/en/", 1)
    )
    assert "LATER-L SAME-PATH" in (auth.ru_text or "")


@pytest.mark.parametrize(
    "mutation",
    ["always-H", "always-B", "planner-loader-split", "H0-to-B", "wrong-nav"],
)
def test_reversible_translate_mutations_are_killed_by_same_snapshot_oracle(
    authority_history: AuthorityHistory,
    mutation: str,
) -> None:
    mode = "current" if mutation == "always-H" else "source-preserving"
    expected_ref = authority_history.baseline if mode == "current" else authority_history.h
    expected_docs = _expected_b_scope() if mode == "current" else EXPECTED_H_DOC_SCOPE
    expect_later_nav = mode == "current"

    healthy_job, healthy_observed = _dry_translate(
        authority_history,
        adapter_mode=mode,
    )
    _assert_translate_snapshot(
        healthy_job,
        healthy_observed,
        authority_history,
        ref=expected_ref,
        expected_docs=expected_docs,
        expect_later_nav=expect_later_nav,
    )

    mutant_job, mutant_observed = _dry_translate(
        authority_history,
        adapter_mode=mode,
        mutation=mutation,
    )
    with pytest.raises(AssertionError):
        _assert_translate_snapshot(
            mutant_job,
            mutant_observed,
            authority_history,
            ref=expected_ref,
            expected_docs=expected_docs,
            expect_later_nav=expect_later_nav,
        )
    if mutation == "planner-loader-split":
        assert mutant_observed.plans[0].doc_ru_paths == EXPECTED_H_DOC_SCOPE

    recovered_job, recovered_observed = _dry_translate(
        authority_history,
        adapter_mode=mode,
    )
    _assert_translate_snapshot(
        recovered_job,
        recovered_observed,
        authority_history,
        ref=expected_ref,
        expected_docs=expected_docs,
        expect_later_nav=expect_later_nav,
    )


def test_reversible_final_ru_mutation_is_caught_by_real_anchor_qa(
    authority_history: AuthorityHistory,
) -> None:
    dependency_en = H_DEPENDENCY.replace("/ru/", "/en/", 1)
    inbound_en = H_SEEDS[0].replace("/ru/", "/en/", 1)

    def assert_healthy(run: CandidateRun) -> None:
        assert run.observed.declaration_refs == [authority_history.h]
        assert "{#client-certificate-prerequisite}" in (
            _text_at(authority_history.repo, authority_history.candidate or "", dependency_en) or ""
        )
        assert not any(
            blocker.path == inbound_en for blocker in run.job.pr_result.final_tree_blockers
        )

    control = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
        adapter_mode="source-preserving",
    )
    assert_healthy(control)

    mutant = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
        adapter_mode="source-preserving",
        declaration_ref_override=authority_history.baseline,
    )
    assert mutant.observed.declaration_refs == [authority_history.baseline]
    assert any(blocker.path == inbound_en for blocker in mutant.job.pr_result.final_tree_blockers)

    recovered = _translate_candidate(
        authority_history,
        keep_broken_dependency=False,
        move_refs=False,
        adapter_mode="source-preserving",
    )
    assert_healthy(recovered)


def test_link_dependency_cap_control_and_reversible_overflow_mutation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "cap-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a05@example.test")
    _git(repo, "config", "user.name", "A05 Contract")
    seed = f"{RU_ROOT}/cap/seed.md"
    _write(repo, seed, "# Seed before links\n")
    for index in range(21):
        _write(repo, f"{RU_ROOT}/cap/d{index:02d}.md", f"# D{index:02d}\n")
    h0 = _commit(repo, "cap H0")
    links = " ".join(f"[d{i:02d}](d{i:02d}.md)" for i in range(21))
    _write(repo, seed, f"# Seed H\n\n{links}\n")
    h = _commit(repo, "cap H")
    _write(repo, seed, "# Seed B\n\n[B only](b-only.md)\n")
    _write(repo, f"{RU_ROOT}/cap/b-only.md", "# B only\n")
    baseline = _commit(repo, "cap later B")

    def make_plan():
        read_ru, read_en, read_ru_base = workflow.make_repo_scope_readers(
            str(repo), baseline, ru_content_ref=h, ru_base_ref=h0
        )
        return workflow.plan_translation_scope(
            [(seed, "modified")],
            read_ru=read_ru,
            read_en_base=read_en,
            read_ru_base=read_ru_base,
            docs_root="ydb/docs",
        )

    plan = make_plan()
    assert len(plan.doc_from_main) == 20
    assert len(plan.doc_ru_paths) == 21
    assert "link dependency budget exhausted" in plan.link_dep_warnings[0]
    assert f"{RU_ROOT}/cap/b-only.md" not in plan.doc_ru_paths

    from ydbdoc_review.navigation.dependency_budget import MarkdownDependencyBudget

    real_init = MarkdownDependencyBudget.__init__

    def overflow(self: MarkdownDependencyBudget, *args: object, **kwargs: object) -> None:
        changed = dict(kwargs)
        changed["limit"] = 21
        real_init(self, *args, **changed)

    with patch.object(MarkdownDependencyBudget, "__init__", new=overflow):
        mutant = make_plan()
    with pytest.raises(AssertionError):
        assert len(mutant.doc_from_main) == 20
    recovered = make_plan()
    assert len(recovered.doc_from_main) == 20
    assert len(recovered.doc_ru_paths) == 21
    assert recovered.link_dep_warnings == plan.link_dep_warnings
