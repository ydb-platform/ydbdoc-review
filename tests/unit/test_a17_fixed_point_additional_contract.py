"""Additional A17 contracts for the breadth-level fixed-point planner."""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.navigation.scope_planner import TranslationScopePlan, plan_translation_scope

DOCS_ROOT = "ydb/docs"
RU_CORE = f"{DOCS_ROOT}/ru/core"


def _en(ru_path: str, *, docs_root: str = DOCS_ROOT) -> str:
    return ru_path.replace(f"{docs_root}/ru/", f"{docs_root}/en/", 1)


@dataclass(frozen=True)
class MemoryTree:
    ru: dict[str, str]
    en: dict[str, str]
    base: dict[str, str]
    docs_root: str = DOCS_ROOT

    def plan(self, changes: list[tuple[str, str]]) -> TranslationScopePlan:
        forbidden_root = DOCS_ROOT if self.docs_root != DOCS_ROOT else None

        def read(mapping: dict[str, str], path: str) -> str | None:
            normalized = path.replace("\\", "/")
            if forbidden_root is not None:
                assert not normalized.startswith(f"{forbidden_root}/")
            return mapping.get(normalized)

        return plan_translation_scope(
            changes,
            read_ru=lambda path: read(self.ru, path),
            read_en_base=lambda path: read(self.en, path),
            read_ru_base=lambda path: read(self.base, path),
            docs_root=self.docs_root,
        )


def test_breadth_level_precedes_global_lexical_order() -> None:
    root = f"{RU_CORE}/a17-extra/breadth/root.md"
    depth_one = tuple(f"{RU_CORE}/a17-extra/breadth/z{i:02d}.md" for i in range(20))
    depth_two = f"{RU_CORE}/a17-extra/breadth/a.md"
    ru = {
        root: "\n".join(f"[z]({path.rsplit('/', 1)[-1]})" for path in depth_one) + "\n",
        **{path: "# Z\n" for path in depth_one},
        depth_two: "# A\n",
    }
    ru[depth_one[0]] += "\n[a](a.md)\n"

    plan = MemoryTree(ru=ru, en={}, base={root: "# Before\n"}).plan([(root, "modified")])

    assert plan.doc_from_main == frozenset(depth_one)
    assert depth_two not in plan.doc_ru_paths
    assert len(plan.link_dep_warnings) == 1
    assert _en(depth_two) in plan.link_dep_warnings[0]


def test_late_toc_sibling_reenters_all_markdown_discoverers() -> None:
    root = f"{RU_CORE}/a17-extra/root.md"
    dependency = f"{RU_CORE}/other-a17-extra/dependency.md"
    sibling = f"{RU_CORE}/other-a17-extra/sibling.md"
    part = f"{RU_CORE}/other-a17-extra/part.md"
    owner = f"{RU_CORE}/other-a17-extra/owner.md"
    section_page = f"{RU_CORE}/other-a17-extra/index.md"
    child_toc = f"{RU_CORE}/other-a17-extra/toc_p.yaml"
    parent_toc = f"{RU_CORE}/toc_p.yaml"
    unrelated_toc = f"{RU_CORE}/unrelated-a17-extra/toc_p.yaml"
    tree = MemoryTree(
        ru={
            root: "[dependency](../other-a17-extra/dependency.md)\n",
            dependency: "# Dependency\n",
            sibling: "{% include [part](part.md) %}\n",
            part: "[owner](owner.md#a17-extra-owner)\n",
            owner: "# Owner {#a17-extra-owner}\n",
            section_page: "# Other section\n",
            child_toc: (
                "items:\n"
                "- name: Dependency\n  href: dependency.md\n"
                "- name: Sibling\n  href: sibling.md\n"
            ),
            parent_toc: (
                "items:\n"
                "- name: Other\n"
                "  href: other-a17-extra/index.md\n"
                "  include:\n"
                "    path: other-a17-extra/toc_p.yaml\n"
                "    mode: link\n"
                "- name: Unrelated\n"
                "  include:\n"
                "    path: unrelated-a17-extra/toc_p.yaml\n"
                "    mode: link\n"
            ),
            unrelated_toc: "items: []\n",
        },
        en={
            _en(owner): "# Owner without declaration\n",
            _en(parent_toc): "items: []\n",
        },
        base={root: "# Before\n"},
    )

    plan = tree.plan([(root, "modified")])

    assert plan.doc_from_main == frozenset(
        {dependency, sibling, part, owner, section_page}
    )
    assert plan.nav_ru_paths == frozenset({child_toc, parent_toc})
    assert unrelated_toc not in plan.nav_ru_paths


def test_budget_denial_cannot_discover_descendant_documents_or_navigation() -> None:
    root = f"{RU_CORE}/a17-extra/denial/root.md"
    admitted = tuple(f"{RU_CORE}/a17-extra/denial/a{i:02d}.md" for i in range(20))
    denied = f"{RU_CORE}/zz-a17-extra/dependency.md"
    child = f"{RU_CORE}/zz-a17-extra/child.md"
    denied_toc = f"{RU_CORE}/zz-a17-extra/toc_p.yaml"
    links = [f"[candidate]({path.rsplit('/', 1)[-1]})" for path in admitted]
    links.append("[denied](../../zz-a17-extra/dependency.md)")
    tree = MemoryTree(
        ru={
            root: "\n".join(links) + "\n",
            **{path: "# Candidate\n" for path in admitted},
            denied: "[child](child.md)\n",
            child: "# Child\n",
            denied_toc: "items:\n- name: Dependency\n  href: dependency.md\n",
        },
        en={},
        base={root: "# Before\n"},
    )

    plan = tree.plan([(root, "modified")])

    assert plan.doc_from_main == frozenset(admitted)
    assert denied not in plan.doc_ru_paths
    assert child not in plan.doc_ru_paths
    assert denied_toc not in plan.nav_ru_paths
    assert len(plan.link_dep_warnings) == 1
    assert _en(denied) in plan.link_dep_warnings[0]


def test_custom_docs_root_is_used_for_dependency_navigation() -> None:
    docs_root = "docs"
    ru_core = f"{docs_root}/ru/core"
    root = f"{ru_core}/a17/root.md"
    dependency = f"{ru_core}/other/dependency.md"
    child_toc = f"{ru_core}/other/toc_p.yaml"
    parent_toc = f"{ru_core}/toc_p.yaml"
    tree = MemoryTree(
        ru={
            root: "[dependency](../other/dependency.md)\n",
            dependency: "# Dependency\n",
            child_toc: "items:\n- name: Dependency\n  href: dependency.md\n",
            parent_toc: (
                "items:\n- name: Other\n  include:\n"
                "    path: other/toc_p.yaml\n    mode: link\n"
            ),
        },
        en={_en(parent_toc, docs_root=docs_root): "items: []\n"},
        base={root: "# Before\n"},
        docs_root=docs_root,
    )

    plan = tree.plan([(root, "modified")])

    assert plan.doc_from_main == frozenset({dependency})
    assert plan.nav_ru_paths == frozenset({child_toc, parent_toc})


def test_redirect_aliases_deduplicate_before_level_admission() -> None:
    first_root = f"{RU_CORE}/a17-extra/redirect/include-root.md"
    second_root = f"{RU_CORE}/a17-extra/redirect/link-root.md"
    alias = f"{RU_CORE}/a17-extra/redirect/alias.md"
    middle = f"{RU_CORE}/a17-extra/redirect/middle.md"
    live = f"{RU_CORE}/a17-extra/redirect/live.md"
    child = f"{RU_CORE}/a17-extra/redirect/child.md"
    redirects = (
        "common:\n"
        "  - from: /a17-extra/redirect/alias.md\n"
        "    to: /a17-extra/redirect/middle.md\n"
        "  - from: /a17-extra/redirect/middle.md\n"
        "    to: /a17-extra/redirect/live.md\n"
    )
    tree = MemoryTree(
        ru={
            first_root: "{% include [alias](alias.md) %}\n",
            second_root: "[middle](middle.md)\n",
            live: "[child](child.md)\n",
            child: "# Child\n",
        },
        en={f"{DOCS_ROOT}/redirects.yaml": redirects},
        base={first_root: "# Before\n", second_root: "# Before\n"},
    )

    plan = tree.plan([(first_root, "modified"), (second_root, "modified")])

    assert plan.doc_from_main == frozenset({live, child})
    assert alias not in plan.doc_ru_paths
    assert middle not in plan.doc_ru_paths
    assert plan.dependency_budget.admitted_ru_paths == frozenset({live, child})

    cycle = (
        "common:\n"
        "  - from: /a17-extra/redirect/cycle-a.md\n"
        "    to: /a17-extra/redirect/cycle-b.md\n"
        "  - from: /a17-extra/redirect/cycle-b.md\n"
        "    to: /a17-extra/redirect/cycle-a.md\n"
    )
    cycle_root = f"{RU_CORE}/a17-extra/redirect/cycle-root.md"
    cycle_plan = MemoryTree(
        ru={
            cycle_root: "[cycle](cycle-a.md)\n",
            f"{RU_CORE}/a17-extra/redirect/cycle-a.md": "# A\n",
            f"{RU_CORE}/a17-extra/redirect/cycle-b.md": "# B\n",
        },
        en={f"{DOCS_ROOT}/redirects.yaml": cycle},
        base={cycle_root: "# Before\n"},
    ).plan([(cycle_root, "modified")])
    assert cycle_plan.doc_from_main == frozenset()
    assert cycle_plan.link_dep_warnings == ()
