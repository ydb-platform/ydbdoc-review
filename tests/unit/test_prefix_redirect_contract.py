"""Contracts for bounded prefix redirects used by dependency planning."""

from __future__ import annotations

from ydbdoc_review.navigation import redirects
from ydbdoc_review.navigation.link_deps import canonical_md_dependency_path
from ydbdoc_review.navigation.scope_planner import plan_translation_scope

DOCS_ROOT = "ydb/docs"
PREFIX = (
    "common:\n"
    "  - from: /reference/embedded-ui/(.*)$\n"
    "    to: /reference/ydb-ui/$1\n"
)


def test_prefix_redirect_contract_and_exact_precedence() -> None:
    step = redirects.prefix_redirect_repo_md_path
    old = f"{DOCS_ROOT}/ru/core/reference/embedded-ui/nested/page.md"
    live = f"{DOCS_ROOT}/ru/core/reference/ydb-ui/nested/page.md"
    assert step(old, PREFIX) == live
    assert step(f"{DOCS_ROOT}/en/core/unrelated.md", PREFIX) == (
        f"{DOCS_ROOT}/en/core/unrelated.md"
    )

    exact = (
        PREFIX
        + "  - from: /reference/embedded-ui/nested/page.md\n"
        + "    to: /reference/special.md\n"
    )
    assert canonical_md_dependency_path(old, redirects_yaml=exact) == (
        f"{DOCS_ROOT}/ru/core/reference/special.md"
    )


def test_longest_conflicts_and_cycles_refuse() -> None:
    old = f"{DOCS_ROOT}/en/core/a/b/page.md"
    conflict = (
        "common:\n"
        "  - from: /a/(.*)$\n    to: /one/$1\n"
        "  - from: /a/b/(.*)$\n    to: /two/$1\n"
        "  - from: /a/b/(.*)$\n    to: /three/$1\n"
    )
    assert canonical_md_dependency_path(old, redirects_yaml=conflict) is None

    cycle = (
        "common:\n"
        "  - from: /a/(.*)$\n    to: /b/$1\n"
        "  - from: /b/(.*)$\n    to: /a/$1\n"
    )
    assert canonical_md_dependency_path(
        f"{DOCS_ROOT}/en/core/a/page.md", redirects_yaml=cycle
    ) is None


def test_prefix_refusals_and_finite_tombstone_membership() -> None:
    step = redirects.prefix_redirect_repo_md_path
    unsafe = "common:\n  - from: /a/(.*)$\n    to: /../escape/$1\n"
    old = f"{DOCS_ROOT}/ru/core/a/page.md"
    assert step(old, unsafe) is None
    assert step(f"{DOCS_ROOT}/ru/core/a/%2e%2e/page.md", PREFIX) is None

    members = redirects.redirect_source_repo_md_paths(
        PREFIX,
        locale="ru",
        candidate_repo_paths=(
            f"{DOCS_ROOT}/ru/core/reference/embedded-ui/page.md",
            f"{DOCS_ROOT}/ru/core/other/page.md",
        ),
    )
    assert members == frozenset(
        {f"{DOCS_ROOT}/ru/core/reference/embedded-ui/page.md"}
    )
    assert not any("(.*)" in path for path in members)


def test_prefix_redirect_to_existing_baseline_en_does_not_admit_successor_ru() -> None:
    root = f"{DOCS_ROOT}/ru/core/guide/root.md"
    old = f"{DOCS_ROOT}/ru/core/reference/embedded-ui/page.md"
    live_en = f"{DOCS_ROOT}/en/core/reference/ydb-ui/page.md"
    reads: list[str] = []
    ru = {root: "[UI](../../reference/embedded-ui/page.md)\n"}
    en = {f"{DOCS_ROOT}/redirects.yaml": PREFIX, live_en: "# Existing EN\n"}

    def read_ru(path: str) -> str | None:
        reads.append(path)
        return ru.get(path)

    plan = plan_translation_scope(
        [(root, "modified")],
        read_ru=read_ru,
        read_en_base=en.get,
        read_ru_base=lambda _path: "# Before\n",
    )

    assert plan.doc_ru_paths == frozenset({root})
    assert old not in reads
    assert f"{DOCS_ROOT}/ru/core/reference/ydb-ui/page.md" not in reads
