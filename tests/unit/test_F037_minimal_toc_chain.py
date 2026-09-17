"""F-037 contracts for minimal connected TOC navigation scope."""

from __future__ import annotations

from ydbdoc_review.navigation.scope_planner import plan_translation_scope

DOCS = "ydb/docs"


def _en(path: str) -> str:
    return path.replace("/ru/", "/en/", 1)


def _plan(files: dict[str, str], base: dict[str, str]):
    return plan_translation_scope(
        [(f"{DOCS}/ru/core/toc_p.yaml", "modified")],
        read_ru=files.get,
        read_en_base=lambda path: {
            _en(f"{DOCS}/ru/core/toc_p.yaml"): (
                "items:\n- name: Overview\n  href: overview.md\n"
            ),
            _en(f"{DOCS}/ru/core/target/toc_p.yaml"): None,
            _en(f"{DOCS}/ru/core/sibling/toc_p.yaml"): None,
            _en(f"{DOCS}/ru/core/overview.md"): "# Overview\n",
        }.get(path),
        read_ru_base=base.get,
    )


def test_F037_toc_chain() -> None:
    parent = f"{DOCS}/ru/core/toc_p.yaml"
    target_toc = f"{DOCS}/ru/core/target/toc_p.yaml"
    target_page = f"{DOCS}/ru/core/target/page.md"
    files = {
        parent: (
            "items:\n"
            "- name: Overview\n  href: overview.md\n"
            "- name: Target\n"
            "  include:\n"
            "    path: target/toc_p.yaml\n"
            "    mode: link\n"
        ),
        target_toc: "items:\n- name: Page\n  href: page.md\n",
        target_page: "# Page\n",
    }
    base = {parent: "items:\n- name: Overview\n  href: overview.md\n"}

    plan = _plan(files, base)

    assert plan.nav_ru_paths == frozenset({parent, target_toc})
    assert target_page in plan.doc_ru_paths


def test_F037_no_siblings() -> None:
    parent = f"{DOCS}/ru/core/toc_p.yaml"
    target_toc = f"{DOCS}/ru/core/target/toc_p.yaml"
    sibling_toc = f"{DOCS}/ru/core/sibling/toc_p.yaml"
    files = {
        parent: (
            "items:\n"
            "- name: Target\n"
            "  include:\n"
            "    path: target/toc_p.yaml\n"
            "    mode: link\n"
            "- name: Sibling\n"
            "  include:\n"
            "    path: sibling/toc_p.yaml\n"
            "    mode: link\n"
        ),
        target_toc: "items:\n- name: Page\n  href: page.md\n",
        sibling_toc: "items:\n- name: Sibling page\n  href: page.md\n",
    }
    base = {
        parent: (
            "items:\n"
            "- name: Sibling\n"
            "  include:\n"
            "    path: sibling/toc_p.yaml\n"
            "    mode: link\n"
        )
    }

    plan = _plan(files, base)

    assert sibling_toc not in plan.nav_ru_paths
    assert f"{DOCS}/ru/core/sibling/page.md" not in plan.doc_ru_paths
