"""A17 RED contract for one bounded dependency/navigation fixed point.

Every non-source Markdown page admitted by the current include, missing-link,
exact-ASCII-fragment-owner, redirect, or navigation rules must be fed back
through those same rules until no new document or navigation path appears.
The fixed point shares A18's single twenty-page admission state: source-diff
roots are free, but every synthetic Markdown page consumes one slot.

The focused planner cases use real parsers and the real scope planner.  The
workflow case below additionally uses immutable Git readers, pair planning,
relation checks, navigation merge/verify, final-tree checks, local commits and
a local bare remote.  Only GitHub, model calls, ops gates, and network push are
replaced at their external boundaries.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github import git_ops, workflow
from ydbdoc_review.github.provenance import parse_authority_evidence
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    plan_translation_scope,
)
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.reporting.builder import result_has_blocking_findings
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.schemas import CriticResponse

DOCS_ROOT = "ydb/docs"
RU_CORE = f"{DOCS_ROOT}/ru/core"
EN_CORE = f"{DOCS_ROOT}/en/core"
RU_A17 = f"{RU_CORE}/a17"
EN_A17 = f"{EN_CORE}/a17"
BUDGET = 20


def _en(ru_path: str) -> str:
    assert ru_path.startswith(f"{DOCS_ROOT}/ru/")
    return ru_path.replace(f"{DOCS_ROOT}/ru/", f"{DOCS_ROOT}/en/", 1)


@dataclass(frozen=True)
class MemoryTree:
    ru: dict[str, str]
    en: dict[str, str]
    base: dict[str, str]

    def plan(
        self,
        changes: list[tuple[str, str]],
    ) -> TranslationScopePlan:
        return plan_translation_scope(
            changes,
            read_ru=lambda path: self.ru.get(path.replace("\\", "/")),
            read_en_base=lambda path: self.en.get(path.replace("\\", "/")),
            read_ru_base=lambda path: self.base.get(path.replace("\\", "/")),
            docs_root=DOCS_ROOT,
        )


def _assert_docs(
    plan: TranslationScopePlan,
    *,
    roots: set[str],
    extras: set[str],
) -> None:
    assert plan.doc_from_diff == frozenset(roots)
    assert plan.doc_from_main == frozenset(extras)
    assert plan.doc_ru_paths == frozenset(roots | extras)


def test_a17_include_dependency_is_rescanned_for_exact_ascii_fragment_owner() -> None:
    root = f"{RU_A17}/include-owner/root.md"
    include = f"{RU_A17}/include-owner/include.md"
    owner = f"{RU_A17}/include-owner/owner.md"
    tree = MemoryTree(
        ru={
            root: "# Root\n\n{% include [part](include.md) %}\n",
            include: "[owner](owner.md#a17-include-owner)\n",
            owner: "# Owner {#a17-include-owner}\n",
        },
        en={_en(owner): "# Owner without the declaration\n"},
        base={root: "# Root before include\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras={include, owner})
    assert plan.link_dep_warnings == ()


def test_a17_fragment_owner_is_rescanned_for_missing_markdown_link() -> None:
    root = f"{RU_A17}/owner-link/root.md"
    owner = f"{RU_A17}/owner-link/owner.md"
    missing = f"{RU_A17}/owner-link/missing.md"
    tree = MemoryTree(
        ru={
            root: "[owner](owner.md#a17-owner-link)\n",
            owner: "# Owner {#a17-owner-link}\n\n[missing](missing.md)\n",
            missing: "# Missing EN dependency\n",
        },
        en={_en(owner): "# Owner without the declaration\n"},
        base={root: "# Root before owner link\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras={owner, missing})
    assert plan.link_dep_warnings == ()


def test_a17_fragment_owner_chain_reaches_second_exact_owner() -> None:
    root = f"{RU_A17}/owner-owner/root.md"
    owner_one = f"{RU_A17}/owner-owner/owner-one.md"
    owner_two = f"{RU_A17}/owner-owner/owner-two.md"
    tree = MemoryTree(
        ru={
            root: "[one](owner-one.md#a17-owner-one)\n",
            owner_one: ("# Owner one {#a17-owner-one}\n\n[two](owner-two.md#a17-owner-two)\n"),
            owner_two: "# Owner two {#a17-owner-two}\n",
        },
        en={
            _en(owner_one): "# Owner one without the declaration\n",
            _en(owner_two): "# Owner two without the declaration\n",
        },
        base={root: "# Root before owner link\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras={owner_one, owner_two})
    assert plan.link_dep_warnings == ()


def test_a17_cross_section_dependency_discovers_child_toc_and_parent_include() -> None:
    root = f"{RU_A17}/cross-section/root.md"
    dependency = f"{RU_CORE}/other-a17/dependency.md"
    child_toc = f"{RU_CORE}/other-a17/toc_p.yaml"
    parent_toc = f"{RU_CORE}/toc_p.yaml"
    tree = MemoryTree(
        ru={
            root: "[other section](../../other-a17/dependency.md)\n",
            dependency: "# Cross-section dependency\n",
            child_toc: ("items:\n- name: Dependency\n  href: dependency.md\n"),
            parent_toc: (
                "items:\n"
                "- name: Other A17\n"
                "  include:\n"
                "    path: other-a17/toc_p.yaml\n"
                "    mode: link\n"
            ),
        },
        en={
            _en(parent_toc): "items: []\n",
        },
        base={root: "# Root before dependency\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras={dependency})
    assert plan.nav_ru_paths == frozenset({child_toc, parent_toc})
    assert plan.nav_from_diff == frozenset()
    assert plan.nav_from_main == frozenset({child_toc, parent_toc})


def test_a17_redirect_retarget_is_rescanned_from_live_page_to_dependency() -> None:
    root = f"{RU_A17}/redirect/root.md"
    old = f"{RU_A17}/redirect/old.md"
    live = f"{RU_A17}/redirect/live.md"
    dependency = f"{RU_A17}/redirect/dependency.md"
    redirects = "common:\n  - from: /a17/redirect/old.md\n    to: /a17/redirect/live.md\n"
    tree = MemoryTree(
        ru={
            root: "[old](old.md)\n",
            old: "# Historical tombstone\n",
            live: "# Live page\n\n[dependency](dependency.md)\n",
            dependency: "# Dependency\n",
        },
        en={f"{DOCS_ROOT}/redirects.yaml": redirects},
        base={root: "# Root before redirect link\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras={live, dependency})
    assert old not in plan.doc_ru_paths
    assert plan.link_dep_warnings == ()


def test_a17_alternating_composite_closes_once_despite_cycles_and_duplicates() -> None:
    root = f"{RU_A17}/composite/root.md"
    include_one = f"{RU_A17}/composite/include-one.md"
    owner_one = f"{RU_A17}/composite/owner-one.md"
    include_two = f"{RU_A17}/composite/include-two.md"
    missing = f"{RU_A17}/composite/missing.md"
    owner_two = f"{RU_A17}/composite/owner-two.md"
    tree = MemoryTree(
        ru={
            root: ("{% include [one](include-one.md) %}\n[duplicate include](include-one.md)\n"),
            include_one: (
                "[owner one](owner-one.md#a17-composite-one)\n"
                "[owner one duplicate](owner-one.md#a17-composite-one)\n"
            ),
            owner_one: ("# Owner one {#a17-composite-one}\n{% include [two](include-two.md) %}\n"),
            include_two: ("[missing](missing.md)\n[cycle](include-one.md)\n"),
            missing: (
                "[owner two](owner-two.md#a17-composite-two)\n"
                "[cycle](owner-one.md#a17-composite-one)\n"
            ),
            owner_two: (
                "# Owner two {#a17-composite-two}\n{% include [duplicate](include-two.md) %}\n"
            ),
        },
        en={
            _en(owner_one): "# Owner one without declaration\n",
            _en(owner_two): "# Owner two without declaration\n",
        },
        base={root: "# Composite root before dependencies\n"},
    )

    plan = tree.plan([(root, "modified")])

    expected = {include_one, owner_one, include_two, missing, owner_two}
    _assert_docs(plan, roots={root}, extras=expected)
    assert len(plan.doc_from_main) == len(expected)
    assert plan.link_dep_warnings == ()


def test_a17_control_link_dependency_include_with_existing_en_stays_out_of_scope() -> None:
    root = f"{RU_A17}/control-link-include/root.md"
    dependency = f"{RU_A17}/control-link-include/dependency.md"
    included = f"{RU_A17}/control-link-include/included.md"
    tree = MemoryTree(
        ru={
            root: "[dependency](dependency.md)\n",
            dependency: "{% include [already English](included.md) %}\n",
            included: "# Included target\n",
        },
        en={_en(included): "# Existing included target\n"},
        base={root: "# Root before link dependency\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras={dependency})
    assert included not in plan.doc_ru_paths
    assert plan.link_dep_warnings == ()


def test_a17_control_existing_en_fragment_declaration_needs_no_owner_repair() -> None:
    root = f"{RU_A17}/control-fragment/root.md"
    owner = f"{RU_A17}/control-fragment/owner.md"
    tree = MemoryTree(
        ru={
            root: "[owner](owner.md#a17-already-present)\n",
            owner: "# Owner {#a17-already-present}\n",
        },
        en={_en(owner): "# Owner {#a17-already-present}\n"},
        base={root: "# Root before stable link\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras=set())
    assert plan.nav_ru_paths == frozenset()


def test_a17_control_unrelated_sidebar_does_not_enter_navigation_scope() -> None:
    root = f"{RU_A17}/control-sidebar/root.md"
    unrelated_page = f"{RU_CORE}/unrelated-a17/page.md"
    unrelated_toc = f"{RU_CORE}/unrelated-a17/toc_p.yaml"
    tree = MemoryTree(
        ru={
            root: "# Changed root\n",
            unrelated_page: "# Unrelated page\n",
            unrelated_toc: "items:\n- name: Unrelated\n  href: page.md\n",
        },
        en={},
        base={root: "# Root before edit\n"},
    )

    plan = tree.plan([(root, "modified")])

    _assert_docs(plan, roots={root}, extras=set())
    assert unrelated_page not in plan.doc_ru_paths
    assert unrelated_toc not in plan.nav_ru_paths
    assert plan.nav_ru_paths == frozenset()


@dataclass(frozen=True)
class BudgetFixture:
    tree: MemoryTree
    roots: tuple[str, ...]
    candidates: tuple[str, ...]
    toc: str


def _budget_fixture(*, reverse_maps: bool = False) -> BudgetFixture:
    roots = tuple(f"{RU_A17}/budget/{family}-root.md" for family in ("include", "link", "owner"))
    candidates = tuple(f"{RU_A17}/budget/candidate-{index:02d}.md" for index in range(21))
    include_candidates = candidates[0::4]
    link_candidates = candidates[1::4]
    owner_candidates = candidates[2::4]
    toc_candidates = candidates[3::4]
    toc = f"{RU_A17}/budget/toc_p.yaml"
    ru: dict[str, str] = {
        roots[0]: "\n".join(
            f"{{% include [candidate]({Path(path).name}) %}}" for path in include_candidates
        )
        + "\n",
        roots[1]: "\n".join(f"[candidate]({Path(path).name})" for path in link_candidates) + "\n",
        roots[2]: "\n".join(
            f"[candidate]({Path(path).name}#a17-budget-{index:02d})"
            for index, path in enumerate(owner_candidates)
        )
        + "\n",
        toc: (
            "items:\n"
            + "".join(f"- name: Candidate\n  href: {Path(path).name}\n" for path in toc_candidates)
        ),
    }
    en: dict[str, str] = {}
    for index, path in enumerate(candidates):
        ru[path] = f"# Candidate {index} {{#a17-budget-{index // 4:02d}}}\n"
        if path in owner_candidates:
            en[_en(path)] = f"# Candidate {index} without declaration\n"
    base = {root: "# Before dependencies\n" for root in roots}
    base[toc] = "items: []\n"
    if reverse_maps:
        ru = dict(reversed(tuple(ru.items())))
        en = dict(reversed(tuple(en.items())))
        base = dict(reversed(tuple(base.items())))
    return BudgetFixture(MemoryTree(ru, en, base), roots, candidates, toc)


def _budget_plan_payload(*, reverse_roots: bool, reverse_maps: bool) -> dict[str, object]:
    fixture = _budget_fixture(reverse_maps=reverse_maps)
    changes: list[tuple[str, str]] = [(root, "modified") for root in fixture.roots]
    changes.append((fixture.toc, "modified"))
    if reverse_roots:
        changes.reverse()
    plan = fixture.tree.plan(changes)
    return {
        "diff": sorted(plan.doc_from_diff),
        "main": sorted(plan.doc_from_main),
        "nav": sorted(plan.nav_ru_paths),
        "warnings": list(plan.link_dep_warnings),
    }


def _assert_one_global_budget_state(payload: dict[str, object]) -> None:
    fixture = _budget_fixture()
    admitted = tuple(payload["main"])
    warnings = tuple(payload["warnings"])
    assert payload["diff"] == sorted(fixture.roots)
    assert payload["nav"] == [fixture.toc]
    assert admitted == fixture.candidates[:BUDGET]
    assert len(admitted) == BUDGET
    assert len(warnings) == 1
    warning = warnings[0]
    denied = fixture.candidates[BUDGET]
    assert "budget" in warning.lower()
    assert "manual" in warning.lower()
    assert denied in warning or _en(denied) in warning


def test_a17_composes_with_a18_as_one_deterministic_twenty_page_admission_state() -> None:
    local_payloads = [
        _budget_plan_payload(reverse_roots=reverse_roots, reverse_maps=reverse_maps)
        for reverse_roots, reverse_maps in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        )
    ]
    assert local_payloads.count(local_payloads[0]) == len(local_payloads)
    _assert_one_global_budget_state(local_payloads[0])

    module_path = Path(__file__).resolve()
    repo_path = module_path.parents[2]
    script = """
import importlib.util
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
repo = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo / "src"))
sys.path.insert(0, str(repo))
spec = importlib.util.spec_from_file_location("a17_hash_probe", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print(json.dumps(module._budget_plan_payload(
    reverse_roots=bool(int(sys.argv[3])),
    reverse_maps=bool(int(sys.argv[4])),
), sort_keys=True))
"""
    subprocess_payloads: list[dict[str, object]] = []
    for seed, reverse_roots, reverse_maps in (
        ("1", False, False),
        ("97", True, False),
        ("211", False, True),
        ("997", True, True),
    ):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(module_path),
                str(repo_path),
                str(int(reverse_roots)),
                str(int(reverse_maps)),
            ],
            cwd=repo_path,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess_payloads.append(json.loads(completed.stdout))
    assert subprocess_payloads.count(local_payloads[0]) == len(subprocess_payloads)
    _assert_one_global_budget_state(subprocess_payloads[-1])


SOURCE_PR = 17
TRANSLATION_PR = 917
REPO_ID = "ydb-platform/ydb"
WORKFLOW_ROOT = f"{RU_A17}/workflow/root.md"
WORKFLOW_INCLUDE_ONE = f"{RU_A17}/workflow/_includes/one.md"
WORKFLOW_OWNER_ONE = f"{RU_A17}/workflow/owner-one.md"
WORKFLOW_OWNER_TWO = f"{RU_A17}/workflow/owner-two.md"
WORKFLOW_INCLUDE_TWO = f"{RU_A17}/workflow/_includes/two.md"
WORKFLOW_OTHER_DEP = f"{RU_CORE}/other-a17-workflow/dependency.md"
WORKFLOW_OLD = f"{RU_A17}/workflow/old.md"
WORKFLOW_LIVE = f"{RU_A17}/workflow/live.md"
WORKFLOW_FINAL = f"{RU_A17}/workflow/final.md"
WORKFLOW_ROOT_TOC = f"{RU_A17}/workflow/toc_p.yaml"
WORKFLOW_OTHER_TOC = f"{RU_CORE}/other-a17-workflow/toc_p.yaml"
WORKFLOW_PARENT_TOC = f"{RU_CORE}/toc_p.yaml"
WORKFLOW_REDIRECTS = f"{DOCS_ROOT}/redirects.yaml"
WORKFLOW_EXTRAS = frozenset(
    {
        WORKFLOW_INCLUDE_ONE,
        WORKFLOW_OWNER_ONE,
        WORKFLOW_OWNER_TWO,
        WORKFLOW_INCLUDE_TWO,
        WORKFLOW_OTHER_DEP,
        WORKFLOW_LIVE,
        WORKFLOW_FINAL,
    }
)
WORKFLOW_NAV = frozenset({WORKFLOW_OTHER_TOC, WORKFLOW_PARENT_TOC})


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_bare(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", f"--git-dir={repo}", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _pull(
    *,
    number: int,
    head_ref: str,
    head_sha: str,
    base_sha: str,
    base_ref: str = "main",
    body: str = "",
    draft: bool = False,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"A17 PR #{number}",
        "body": body,
        "merged": False,
        "state": "open",
        "draft": draft,
        "merge_commit_sha": None,
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {
                "clone_url": f"https://github.com/{REPO_ID}.git",
                "full_name": REPO_ID,
                "owner": {"login": REPO_ID.split("/", 1)[0]},
                "name": REPO_ID.split("/", 1)[1],
            },
        },
        "base": {"ref": base_ref, "sha": base_sha},
    }


@dataclass
class WorkflowHistory:
    producer: Path
    upstream: Path
    h0: str
    h: str
    candidate_shas: list[str] = field(default_factory=list)


def _workflow_history(tmp_path: Path) -> WorkflowHistory:
    producer = tmp_path / "a17-workflow-producer"
    upstream = tmp_path / "a17-workflow-upstream.git"
    producer.mkdir()
    _git(producer, "init", "-b", "main")
    _git(producer, "config", "user.email", "a17@example.test")
    _git(producer, "config", "user.name", "A17 Contract")

    ru_files = {
        WORKFLOW_INCLUDE_ONE: (
            "[owner one](../owner-one.md#a17-workflow-one)\n"
            "[owner one duplicate](../owner-one.md#a17-workflow-one)\n"
        ),
        WORKFLOW_OWNER_ONE: (
            "# Owner one {#a17-workflow-one}\n\n"
            "[owner two](owner-two.md#a17-workflow-two)\n\n"
            "{% include [two](_includes/two.md) %}\n"
        ),
        WORKFLOW_OWNER_TWO: (
            "# Owner two {#a17-workflow-two}\n\n{% include [two again](_includes/two.md) %}\n"
        ),
        WORKFLOW_INCLUDE_TWO: (
            "[other dependency](../../../other-a17-workflow/dependency.md)\n[cycle](one.md)\n"
        ),
        WORKFLOW_OTHER_DEP: ("# Other dependency\n\n[live page](../a17/workflow/live.md)\n"),
        WORKFLOW_OLD: "# Historical redirect tombstone\n",
        WORKFLOW_LIVE: "# Live page\n\n[final dependency](final.md)\n",
        WORKFLOW_FINAL: (
            "# Final dependency\n\n"
            "[cycle to owner](owner-one.md#a17-workflow-one)\n"
            "[duplicate cycle](_includes/one.md)\n"
        ),
        WORKFLOW_ROOT_TOC: (
            "items:\n"
            "- name: Root\n  href: root.md\n"
            "- name: Owner one\n  href: owner-one.md\n"
            "- name: Owner two\n  href: owner-two.md\n"
            "- name: Redirect tombstone\n  href: old.md\n"
            "- name: Live\n  href: live.md\n"
            "- name: Final\n  href: final.md\n"
        ),
        WORKFLOW_OTHER_TOC: ("items:\n- name: Other dependency\n  href: dependency.md\n"),
        WORKFLOW_PARENT_TOC: (
            "items:\n"
            "- name: A17 workflow\n"
            "  include:\n"
            "    path: a17/workflow/toc_p.yaml\n"
            "    mode: link\n"
            "- name: Other A17 workflow\n"
            "  include:\n"
            "    path: other-a17-workflow/toc_p.yaml\n"
            "    mode: link\n"
        ),
        WORKFLOW_REDIRECTS: (
            "common:\n  - from: /a17/workflow/old.md\n    to: /a17/workflow/live.md\n"
        ),
    }
    for path, text in ru_files.items():
        _write(producer, path, text)

    _write(producer, _en(WORKFLOW_OWNER_ONE), "# Owner one without declaration\n")
    _write(producer, _en(WORKFLOW_OWNER_TWO), "# Owner two without declaration\n")
    _write(producer, _en(WORKFLOW_ROOT_TOC), ru_files[WORKFLOW_ROOT_TOC])
    _write(
        producer,
        _en(WORKFLOW_PARENT_TOC),
        (
            "items:\n"
            "- name: A17 workflow\n"
            "  include:\n"
            "    path: a17/workflow/toc_p.yaml\n"
            "    mode: link\n"
        ),
    )
    h0 = _commit(producer, "A17 committed dependency graph baseline")

    _write(
        producer,
        WORKFLOW_ROOT,
        "# Workflow root\n\n{% include [one](_includes/one.md) %}\n",
    )
    h = _commit(producer, "A17 source root")
    _git(producer, "branch", "source-a17", h)
    _git(producer, "checkout", "--detach", h)
    _git(producer, "branch", "-f", "main", h0)
    _git(producer, "init", "--bare", str(upstream))
    _git(producer, "remote", "add", "origin", str(upstream))
    _git(producer, "push", "origin", f"{h0}:refs/heads/main")
    _git(producer, "push", "origin", f"{h}:refs/heads/source-a17")
    _git(producer, "fetch", "origin")
    return WorkflowHistory(producer, upstream, h0, h)


@dataclass
class A17GitHub:
    history: WorkflowHistory
    translation_body: str = ""
    remote_heads: dict[str, str | None] = field(default_factory=dict)
    comments: list[tuple[int, str]] = field(default_factory=list)
    body_reads: list[str] = field(default_factory=list)
    draft_prs: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.remote_heads.setdefault(self.branch, None)

    @property
    def branch(self) -> str:
        return f"ydbdoc-review/pr-{SOURCE_PR}"

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        assert f"{owner}/{repo}" == REPO_ID
        if number == SOURCE_PR:
            return _pull(
                number=SOURCE_PR,
                head_ref="source-a17",
                head_sha=self.history.h,
                base_sha=self.history.h0,
            )
        assert number == TRANSLATION_PR
        head = self.remote_heads[self.branch]
        assert head is not None
        self.body_reads.append(self.translation_body)
        return _pull(
            number=TRANSLATION_PR,
            head_ref=self.branch,
            head_sha=head,
            base_sha=self.history.h,
            base_ref="source-a17",
            body=self.translation_body,
            draft=TRANSLATION_PR in self.draft_prs,
        )

    def iter_pull_files(self, owner: str, repo: str, number: int) -> Iterator[dict[str, str]]:
        assert f"{owner}/{repo}" == REPO_ID
        if number == SOURCE_PR:
            yield {"filename": WORKFLOW_ROOT, "status": "added"}
            return
        assert number == TRANSLATION_PR
        head = self.remote_heads[self.branch]
        assert head is not None
        output = _git_bare(self.history.upstream, "diff", "--name-status", self.history.h0, head)
        for row in output.splitlines():
            status, path = row.split("\t", 1)
            mapped = "added" if status == "A" else "removed" if status == "D" else "modified"
            yield {"filename": path, "status": mapped}

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str | None:
        assert f"{owner}/{repo}" == REPO_ID
        return self.remote_heads.setdefault(branch, None)

    def find_open_pull_by_head(
        self, owner: str, repo: str, *, head_branch: str, base: str
    ) -> tuple[str, int] | None:
        assert f"{owner}/{repo}" == REPO_ID
        assert head_branch == self.branch
        assert base == "source-a17"
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
        assert title == f"Auto-translate docs from PR #{SOURCE_PR}"
        assert head == self.branch
        assert base == "source-a17"
        self.translation_body = body
        if draft:
            self.draft_prs.add(TRANSLATION_PR)
        return f"https://github.com/{REPO_ID}/pull/{TRANSLATION_PR}", TRANSLATION_PR, True

    def update_pull_body(self, owner: str, repo: str, number: int, body: str) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == TRANSLATION_PR
        self.translation_body = body

    def post_issue_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        assert f"{owner}/{repo}" == REPO_ID
        self.comments.append((number, body))
        return f"https://github.com/{REPO_ID}/issues/{number}#a17"

    def iter_issue_comments(
        self, owner: str, repo: str, number: int
    ) -> Iterator[dict[str, object]]:
        assert f"{owner}/{repo}" == REPO_ID
        return iter(())

    def add_issue_labels(self, owner: str, repo: str, number: int, labels: list[str]) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == TRANSLATION_PR
        assert labels == ["documentation"]

    def convert_pull_to_draft(self, owner: str, repo: str, number: int) -> None:
        assert f"{owner}/{repo}" == REPO_ID
        assert number == TRANSLATION_PR
        self.draft_prs.add(number)

    def delete_branch(self, owner: str, repo: str, branch: str) -> bool:
        assert f"{owner}/{repo}" == REPO_ID
        return False


class BoundaryClient:
    def __init__(self) -> None:
        self.usage_tracker = UsageTracker()
        self.transcript_recorder = None

    def model_chain_for_role(self, role: str) -> list[str]:
        assert role == "translate"
        return ["a17-fixture-translate"]

    def chat(self, messages: list[dict[str, str]], **_kwargs: object) -> SimpleNamespace:
        request = json.loads(messages[-1]["content"])
        labels = request.get("labels") or []
        response = {"translations": [{"ru": label, "en": f"English {label}"} for label in labels]}
        return SimpleNamespace(content=json.dumps(response))


@dataclass
class WorkflowObservation:
    plans: list[TranslationScopePlan] = field(default_factory=list)
    translated_files: list[str] = field(default_factory=list)
    critic_files: list[str] = field(default_factory=list)
    inline_jobs: list[workflow.DocJobResult] = field(default_factory=list)


def _workflow_config():
    config = load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "a17-folder",
            "YDBDOC_YC_API_KEY": "a17-key",
            "GITHUB_TOKEN": "a17-token",
            "YDBDOC_TRANSLATION_RU_AUTHORITY_MODE": "current",
        }
    )
    return config.model_copy(
        update={
            "paths": config.paths.model_copy(update={"translate_skip_globs": []}),
        }
    )


@contextmanager
def _workflow_runtime(
    history: WorkflowHistory,
    github: A17GitHub,
    observed: WorkflowObservation,
) -> Iterator[None]:
    real_plan = workflow.plan_translation_scope
    real_prepare = workflow.prepare_translation_branch_on_base
    real_inline_verify = workflow.run_doc_verify

    def capture_plan(*args: object, **kwargs: object) -> TranslationScopePlan:
        plan = real_plan(*args, **kwargs)
        observed.plans.append(plan)
        return plan

    def translate_segments(segments: list[Any], *_args: object, **kwargs: object) -> dict[str, str]:
        observed.translated_files.append(str(kwargs["file_path"]))
        return {segment.id: segment.text for segment in segments}

    def critic_ok(*_args: object, **kwargs: object) -> CriticResponse:
        observed.critic_files.append(str(kwargs["file_path"]))
        return CriticResponse(verdict="ok", issues=[])

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
        assert source_sha is not None
        resolved = _git(Path(repo_path), "rev-parse", f"{source_sha}^{{commit}}")
        before = github.remote_heads.get(branch)
        assert before == expected_remote_sha
        status = (
            git_ops.RefMutationStatus.NOOP
            if before == resolved
            else git_ops.RefMutationStatus.CHANGED
        )
        _git(
            Path(repo_path),
            "push",
            "--force",
            str(history.upstream),
            f"{resolved}:refs/heads/{branch}",
        )
        github.remote_heads[branch] = resolved
        if status is git_ops.RefMutationStatus.CHANGED:
            history.candidate_shas.append(resolved)
        return git_ops.RefMutationReceipt(
            lease=git_ops.RemoteRefLease(branch=branch, expected_sha=expected_remote_sha),
            operation=git_ops.RefMutationOperation.UPDATE,
            requested_sha=resolved,
            status=status,
            porcelain_flag="+" if status is git_ops.RefMutationStatus.CHANGED else "=",
            stdout="local A17 bare-remote push",
            stderr="",
        )

    def capture_inline_verify(**kwargs: object) -> workflow.DocJobResult:
        # Keep the producer's single real commit immutable.  Inline verify still
        # runs its real readers, pair/relation QA, nav verification and final
        # checks; the independent fresh-clone verify below is the second reader.
        forwarded = dict(kwargs)
        forwarded["no_commit"] = True
        job = real_inline_verify(**forwarded)
        observed.inline_jobs.append(job)
        return job

    with ExitStack() as stack:
        stack.enter_context(patch.object(workflow, "GitHubClient", return_value=github))
        stack.enter_context(
            patch.object(
                workflow,
                "begin_ops_job",
                return_value=(None, GateResult(ok=True), None),
            )
        )
        stack.enter_context(
            patch.object(workflow, "create_llm_client", return_value=BoundaryClient())
        )
        stack.enter_context(
            patch.object(workflow, "load_glossary", return_value=Glossary(entries=[]))
        )
        stack.enter_context(
            patch.object(workflow, "plan_translation_scope", side_effect=capture_plan)
        )
        stack.enter_context(
            patch("ydbdoc_review.harness.steps.translate_segments", side_effect=translate_segments)
        )
        stack.enter_context(
            patch("ydbdoc_review.harness.steps.run_critic_pass", side_effect=critic_ok)
        )
        stack.enter_context(patch("ydbdoc_review.harness.steps.run_verify", side_effect=critic_ok))
        stack.enter_context(
            patch.object(workflow, "prepare_translation_branch_on_base", side_effect=prepare)
        )
        stack.enter_context(patch.object(workflow, "push_branch", side_effect=local_push))
        stack.enter_context(
            patch.object(workflow, "run_doc_verify", side_effect=capture_inline_verify)
        )
        yield


def _fresh_consumer(tmp_path: Path, history: WorkflowHistory) -> Path:
    consumer = tmp_path / "a17-workflow-consumer"
    subprocess.run(
        [
            "git",
            "clone",
            "--branch",
            f"ydbdoc-review/pr-{SOURCE_PR}",
            "--single-branch",
            f"file://{history.upstream}",
            str(consumer),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(consumer, "config", "user.email", "a17-consumer@example.test")
    _git(consumer, "config", "user.name", "A17 Consumer")
    _git(consumer, "fetch", "origin", "main:refs/remotes/origin/main")
    return consumer


def _assert_preserved_contract_links(repo: Path, ref: str) -> None:
    expected_links = {
        _en(WORKFLOW_INCLUDE_ONE): "(../owner-one.md#a17-workflow-one)",
        _en(WORKFLOW_OWNER_ONE): "(owner-two.md#a17-workflow-two)",
        _en(WORKFLOW_INCLUDE_TWO): "(../../../other-a17-workflow/dependency.md)",
        _en(WORKFLOW_OTHER_DEP): "(../a17/workflow/live.md)",
        _en(WORKFLOW_LIVE): "(final.md)",
        _en(WORKFLOW_FINAL): "(owner-one.md#a17-workflow-one)",
    }
    for path, href in expected_links.items():
        text = _git(repo, "show", f"{ref}:{path}")
        assert href in text, path


def test_a17_real_translate_commit_and_independent_verify_use_the_same_closed_plan(
    tmp_path: Path,
) -> None:
    history = _workflow_history(tmp_path)
    producer_github = A17GitHub(history)
    producer_observed = WorkflowObservation()

    with _workflow_runtime(history, producer_github, producer_observed):
        producer = workflow.run_doc_translate(
            repo_path=str(history.producer),
            github_repo=REPO_ID,
            pr_number=SOURCE_PR,
            merge_base_with="origin/main",
            config=_workflow_config(),
        )

    assert producer.committed and producer.pushed
    assert producer.translation_pr_number == TRANSLATION_PR
    assert not result_has_blocking_findings(producer.pr_result)
    assert len(history.candidate_shas) == 1
    candidate = history.candidate_shas[0]
    assert _git(history.producer, "rev-parse", f"{candidate}^") == history.h
    assert producer_observed.inline_jobs
    assert not result_has_blocking_findings(producer_observed.inline_jobs[-1].pr_result)

    body = producer_github.translation_body
    evidence = parse_authority_evidence(body)
    assert evidence.authority.source_head_sha == history.h
    assert evidence.candidate_sha == candidate

    consumer = _fresh_consumer(tmp_path, history)
    consumer_github = A17GitHub(
        history,
        translation_body=body,
        remote_heads={f"ydbdoc-review/pr-{SOURCE_PR}": candidate},
    )
    consumer_observed = WorkflowObservation()
    with _workflow_runtime(history, consumer_github, consumer_observed):
        verified = workflow.run_doc_verify(
            repo_path=str(consumer),
            github_repo=REPO_ID,
            pr_number=TRANSLATION_PR,
            merge_base_with="origin/main",
            config=_workflow_config(),
            skip_ops_gates=True,
            no_commit=True,
        )

    assert not result_has_blocking_findings(verified.pr_result)
    assert not verified.committed
    assert not verified.pushed
    assert producer_observed.plans
    for plan in producer_observed.plans:
        _assert_docs(plan, roots={WORKFLOW_ROOT}, extras=set(WORKFLOW_EXTRAS))
        assert plan.nav_ru_paths == WORKFLOW_NAV
        assert plan.nav_from_diff == frozenset()
        assert plan.nav_from_main == WORKFLOW_NAV
        assert plan.link_dep_warnings == ()
    assert consumer_observed.plans
    for plan in consumer_observed.plans:
        _assert_docs(plan, roots={WORKFLOW_ROOT}, extras=set(WORKFLOW_EXTRAS))
        assert plan.nav_ru_paths == WORKFLOW_NAV
        assert plan.nav_from_main == WORKFLOW_NAV

    expected_en_docs = {_en(WORKFLOW_ROOT), *(_en(path) for path in WORKFLOW_EXTRAS)}
    changed = {
        row.split("\t", 1)[-1]
        for row in _git(
            history.producer, "diff", "--name-status", history.h, candidate
        ).splitlines()
    }
    assert expected_en_docs <= changed
    assert {_en(path) for path in WORKFLOW_NAV} <= changed
    assert _en(WORKFLOW_OLD) not in changed
    assert consumer_github.body_reads == [body]
    assert _git(consumer, "rev-parse", "HEAD") == candidate
    _assert_preserved_contract_links(history.producer, candidate)
    _assert_preserved_contract_links(consumer, candidate)
