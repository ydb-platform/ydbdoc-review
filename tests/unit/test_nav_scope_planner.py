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

CASE_43997_DIFF_RU = frozenset(
    {
        "ydb/docs/ru/core/recipes/ydb-sdk/auth-access-token.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/auth-anonymous.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/auth-env.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/auth-metadata.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/auth-service-account.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/auth-static.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/balancing-prefer-local.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/balancing-prefer-location.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/balancing-random-choice.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/bulk-upsert.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/distributed-lock.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/init.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/retry.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/session-pool-limit.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/ttl.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/tx-control.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/upsert.md",
        "ydb/docs/ru/core/recipes/ydb-sdk/vector-search.md",
        "ydb/docs/ru/core/reference/ydb-sdk/coordination.md",
        "ydb/docs/ru/core/reference/ydb-sdk/observability/logging/logging.md",
    }
)

_CROSS_SECTION_JUNK = (
    "json-search",
    "streaming-query",
    "integrations/spring",
    "integrations/sql-translation",
)

_SIBLING_JUNK = ("sqs-api", "kafka-api", "configuration", "embedded-ui")


def _load_case(case_id: str):
    case_dir = FIXTURES / case_id
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    file_index: dict[str, Path] = {}
    for repo_path, meta in manifest["files"].items():
        if meta.get("missing"):
            continue
        file_index[repo_path] = FIXTURES / meta["path"]

    en_present = set(manifest.get("en_present_at_base") or [])
    ru_base_files: dict[str, Path] = {}
    for repo_path, meta in (manifest.get("ru_at_base") or {}).items():
        if meta.get("missing"):
            continue
        ru_base_files[repo_path] = FIXTURES / meta["path"]

    def read_ru(repo_path: str) -> str | None:
        path = file_index.get(repo_path)
        if path is None or not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def read_ru_base(repo_path: str) -> str | None:
        path = ru_base_files.get(repo_path)
        if path is not None and path.is_file():
            return path.read_text(encoding="utf-8")
        return read_ru(repo_path)

    def read_en_base(repo_path: str) -> str | None:
        if repo_path in en_present:
            return "# stub EN exists\n"
        return None

    return manifest, read_ru, read_en_base, read_ru_base


def _plan(case_id: str):
    manifest, read_ru, read_en_base, read_ru_base = _load_case(case_id)
    changes = changes_from_manifest(manifest["pr_diff_ru"])
    return plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en_base,
        read_ru_base=read_ru_base,
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


def test_case_43997_scope_is_exactly_diff():
    """PR #43997 — Java SDK snippets; no cross-section absent-EN mirror."""
    plan = _plan("case_43997")

    assert plan.doc_from_main == frozenset()
    assert plan.doc_ru_paths == CASE_43997_DIFF_RU
    assert plan.doc_from_diff == CASE_43997_DIFF_RU
    for junk in _CROSS_SECTION_JUNK:
        assert not any(junk in p for p in plan.doc_ru_paths)


def test_case_46577_regression_matches_43997_diff_only():
    """Translation PR #46577 must not repeat 36-file scope overrun."""
    plan = _plan("case_43997")
    pairs = doc_pairs_from_plan(plan)
    assert len(pairs) == len(CASE_43997_DIFF_RU)
    assert {p.ru_path for p in pairs} == CASE_43997_DIFF_RU


def test_case_45181_does_not_pull_sibling_sqs_api():
    """PR #45181 — diff pages only; sibling reference sections stay out of scope."""
    plan = _plan("case_45181")

    assert plan.doc_ru_paths == frozenset(
        {
            "ydb/docs/ru/core/reference/ydb-sdk/topic.md",
            "ydb/docs/ru/core/devops/observability/diagnostics.md",
        }
    )
    assert plan.doc_from_diff == plan.doc_ru_paths
    assert plan.doc_from_main == frozenset()
    for junk in _SIBLING_JUNK:
        assert not any(junk in p for p in plan.doc_ru_paths | plan.nav_ru_paths)


def test_planned_toc_extras_for_pair_case_45181():
    """J.6: merge extras follow scope plan; sqs-api siblings excluded."""
    manifest, read_ru, read_en_base, read_ru_base = _load_case("case_45181")
    changes = changes_from_manifest(manifest["pr_diff_ru"])
    plan = plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en_base,
        read_ru_base=read_ru_base,
    )

    ref_toc = read_ru("ydb/docs/ru/core/reference/toc_p.yaml")
    assert ref_toc is not None
    ref_hrefs, ref_includes = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/reference/toc_p.yaml",
        ref_toc,
    )
    assert ref_hrefs == set()
    assert "sqs-api/toc_p.yaml" not in ref_includes
    assert "ydb-sdk/toc_p.yaml" in ref_includes

    ydb_sdk_toc_i = read_ru("ydb/docs/ru/core/reference/ydb-sdk/toc_i.yaml")
    assert ydb_sdk_toc_i is not None
    sdk_hrefs, sdk_includes = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/reference/ydb-sdk/toc_i.yaml",
        ydb_sdk_toc_i,
    )
    assert sdk_hrefs == {"topic.md"}
    assert sdk_includes == set()


def test_case_44820_plans_sqs_from_direct_diff():
    """PR #44820: SQS pages and reference toc in PR diff."""
    plan = _plan("case_44820")

    assert plan.doc_ru_paths == frozenset(
        {
            "ydb/docs/ru/core/reference/sqs-api/index.md",
            "ydb/docs/ru/core/reference/sqs-api/auth.md",
            "ydb/docs/ru/core/reference/sqs-api/examples.md",
            "ydb/docs/ru/core/reference/sqs-api/_includes/limitations.md",
            "ydb/docs/ru/core/reference/sqs-api/_includes/examples_prerequisites.md",
        }
    )
    assert "ydb/docs/ru/core/reference/sqs-api/index.md" in plan.doc_from_diff
    assert plan.doc_from_main == frozenset()
    assert "ydb/docs/ru/core/reference/toc_p.yaml" in plan.nav_from_diff
    assert "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml" in plan.nav_from_main


def test_case_43530_plans_observability_tocs_from_diff():
    """PR #43530: explicit toc edits in diff."""
    manifest, read_ru, read_en_base, read_ru_base = _load_case("case_43530")
    changes = changes_from_manifest(manifest["pr_diff_ru"])
    plan = plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en_base,
        read_ru_base=read_ru_base,
    )

    observability_tocs = {
        "ydb/docs/ru/core/reference/ydb-sdk/observability/toc_p.yaml",
        "ydb/docs/ru/core/reference/ydb-sdk/observability/logging/toc_p.yaml",
        "ydb/docs/ru/core/reference/ydb-sdk/observability/metrics/toc_p.yaml",
        "ydb/docs/ru/core/reference/ydb-sdk/observability/tracing/toc_p.yaml",
    }
    assert observability_tocs <= plan.nav_from_diff


def test_case_44457_scoped_to_diff_not_whole_menu():
    """PR #44457: 4 RU files in source PR — not every missing EN href in ancestor tocs."""
    plan = _plan("case_44457")

    expected_docs = frozenset(
        {
            "ydb/docs/ru/core/concepts/glossary.md",
            "ydb/docs/ru/core/concepts/query_execution/execution_process.md",
            "ydb/docs/ru/core/concepts/query_execution/index.md",
        }
    )
    assert plan.doc_ru_paths == expected_docs
    assert plan.doc_from_main == frozenset()

    spurious = {
        "ydb/docs/ru/core/postgresql/connect.md",
        "ydb/docs/ru/core/concepts/secondary_indexes.md",
        "ydb/docs/ru/core/public-materials/podcasts.md",
        "ydb/docs/ru/core/reference/configuration/hive_config.md",
    }
    assert not (spurious & plan.doc_ru_paths)

    assert "ydb/docs/ru/core/concepts/query_execution/toc_i.yaml" in plan.nav_from_diff


@pytest.mark.parametrize(
    "case_id",
    ["case_43997", "case_45181", "case_44820", "case_43530", "case_44457"],
)
def test_planner_doc_and_nav_disjoint_kinds(case_id: str):
    plan = _plan(case_id)
    doc = {p for p in plan.doc_ru_paths if p.endswith(".md")}
    nav = {p for p in plan.nav_ru_paths if p.endswith((".yaml", ".yml"))}
    assert plan.doc_ru_paths == doc
    assert plan.nav_ru_paths == nav
    assert not (doc & nav)
