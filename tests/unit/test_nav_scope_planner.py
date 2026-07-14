"""Golden tests for unified navigation scope planner (§22).

Fixtures: ``tests/fixtures/nav_cases/`` — real ydb PR snapshots from
``scripts/fetch_nav_fixtures.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    changes_from_manifest,
    doc_pairs_from_plan,
    navigation_pairs_from_plan,
    plan_translation_scope,
    planned_toc_extras_for_pair,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nav_cases"


def _load_case(case_id: str):
    case_dir = FIXTURES / case_id
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    file_index: dict[str, Path] = {}
    for repo_path, meta in manifest["files"].items():
        if meta.get("missing"):
            continue
        file_index[repo_path] = FIXTURES / meta["path"]

    def read_ru(repo_path: str) -> str | None:
        path = file_index.get(repo_path)
        if path is None or not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    en_present = set(manifest.get("en_present_at_base") or [])

    def read_en_base(repo_path: str) -> str | None:
        if repo_path in en_present:
            return "# stub EN exists\n"
        return None

    return manifest, read_ru, read_en_base


def _plan(case_id: str):
    manifest, read_ru, read_en_base = _load_case(case_id)
    changes = changes_from_manifest(manifest["pr_diff_ru"])
    return plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en_base,
    )


def test_doc_pairs_from_plan_skips_bilingual_en():
    plan = plan_translation_scope(
        changes_from_manifest(["ydb/docs/ru/core/reference/ydb-sdk/topic.md"]),
        read_ru=lambda p: "# x",
        read_en_base=lambda p: None,
    )
    pairs = doc_pairs_from_plan(
        plan,
        skip_en_paths=frozenset(
            {"ydb/docs/en/core/reference/ydb-sdk/topic.md"}
        ),
    )
    assert pairs == []


def test_navigation_pairs_from_plan_marks_supplement_only():
    plan = TranslationScopePlan(
        doc_ru_paths=frozenset(),
        doc_from_diff=frozenset(),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(
            {
                "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml",
                "ydb/docs/ru/core/reference/toc_p.yaml",
            }
        ),
        nav_from_diff=frozenset({"ydb/docs/ru/core/reference/toc_p.yaml"}),
        nav_from_main=frozenset({"ydb/docs/ru/core/reference/sqs-api/toc_p.yaml"}),
    )
    pairs = navigation_pairs_from_plan(plan)
    by_ru = {p.ru_path: p for p in pairs}
    assert by_ru["ydb/docs/ru/core/reference/sqs-api/toc_p.yaml"].supplement_only
    assert not by_ru["ydb/docs/ru/core/reference/toc_p.yaml"].supplement_only


def test_case_45181_plans_sqs_api_closure_from_topic_diff():
    """PR #45181: only topic+d diagnostics in diff; sqs-api tree from toc includes."""
    plan = _plan("case_45181")

    assert "ydb/docs/ru/core/reference/ydb-sdk/topic.md" in plan.doc_from_diff
    assert "ydb/docs/ru/core/devops/observability/diagnostics.md" in plan.doc_from_diff

    sqs_pages = {
        "ydb/docs/ru/core/reference/sqs-api/index.md",
        "ydb/docs/ru/core/reference/sqs-api/auth.md",
        "ydb/docs/ru/core/reference/sqs-api/examples.md",
    }
    assert sqs_pages <= plan.doc_from_main

    sqs_includes = {
        "ydb/docs/ru/core/reference/sqs-api/_includes/limitations.md",
        "ydb/docs/ru/core/reference/sqs-api/_includes/examples_prerequisites.md",
    }
    assert sqs_includes <= plan.doc_from_main

    sqs_tocs = {
        "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml",
        "ydb/docs/ru/core/reference/sqs-api/toc_i.yaml",
    }
    assert sqs_tocs <= plan.nav_from_main

    assert "ydb/docs/ru/core/reference/ydb-sdk/toc_i.yaml" in plan.nav_ru_paths
    assert "ydb/docs/ru/core/devops/observability/toc_p.yaml" in plan.nav_ru_paths


def test_planned_toc_extras_for_pair_case_45181():
    """J.6: merge extras come from plan, not post-translate basename intersection."""
    manifest, read_ru, read_en_base = _load_case("case_45181")
    changes = changes_from_manifest(manifest["pr_diff_ru"])
    plan = plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en_base,
    )

    sqs_toc_p = read_ru("ydb/docs/ru/core/reference/sqs-api/toc_p.yaml")
    assert sqs_toc_p is not None
    hrefs, includes = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml",
        sqs_toc_p,
    )
    assert hrefs == {"index.md"}
    assert includes == {"toc_i.yaml"}

    sqs_toc_i = read_ru("ydb/docs/ru/core/reference/sqs-api/toc_i.yaml")
    assert sqs_toc_i is not None
    hrefs_i, includes_i = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/reference/sqs-api/toc_i.yaml",
        sqs_toc_i,
    )
    assert hrefs_i == {"auth.md", "examples.md"}
    assert includes_i == set()

    ref_toc = read_ru("ydb/docs/ru/core/reference/toc_p.yaml")
    assert ref_toc is not None
    ref_hrefs, ref_includes = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/reference/toc_p.yaml",
        ref_toc,
    )
    assert ref_hrefs == {"sqs-api/index.md"}
    assert "sqs-api/toc_p.yaml" in ref_includes
    assert "ydb-sdk/toc_p.yaml" in ref_includes


def test_case_44820_plans_sqs_from_direct_diff():
    """PR #44820: SQS pages and reference toc in PR diff."""
    plan = _plan("case_44820")

    assert "ydb/docs/ru/core/reference/sqs-api/index.md" in plan.doc_from_diff
    assert "ydb/docs/ru/core/reference/toc_p.yaml" in plan.nav_from_diff
    assert "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml" in plan.nav_from_main


def test_case_43530_plans_observability_tocs_from_diff():
    """PR #43530: explicit toc edits in diff."""
    manifest, read_ru, read_en_base = _load_case("case_43530")
    changes = changes_from_manifest(manifest["pr_diff_ru"])
    plan = plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en_base,
    )

    observability_tocs = {
        "ydb/docs/ru/core/reference/ydb-sdk/observability/toc_p.yaml",
        "ydb/docs/ru/core/reference/ydb-sdk/observability/logging/toc_p.yaml",
        "ydb/docs/ru/core/reference/ydb-sdk/observability/metrics/toc_p.yaml",
        "ydb/docs/ru/core/reference/ydb-sdk/observability/tracing/toc_p.yaml",
    }
    assert observability_tocs <= plan.nav_from_diff


@pytest.mark.parametrize(
    "case_id",
    ["case_45181", "case_44820", "case_43530"],
)
def test_planner_doc_and_nav_disjoint_kinds(case_id: str):
    plan = _plan(case_id)
    doc = {p for p in plan.doc_ru_paths if p.endswith(".md")}
    nav = {p for p in plan.nav_ru_paths if p.endswith((".yaml", ".yml"))}
    assert plan.doc_ru_paths == doc
    assert plan.nav_ru_paths == nav
    assert not (doc & nav)
