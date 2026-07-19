"""PR-based TOC regression catalog — every known toc failure mode from ydb PRs.

Maps production failures → deterministic unit checks. Keep in sync with
``docs/memory-bank/09-navigation-scope.md`` §22.14.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from ydbdoc_review.navigation.scope_planner import plan_translation_scope
from ydbdoc_review.navigation.toc import (
    merge_en_toc_yaml,
    parse_toc_items,
    toc_translate_scope,
    validate_toc_merge,
)
from ydbdoc_review.validation.toc_targets import (
    check_missing_toc_targets,
    check_orphan_translated_pages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return str(repo)


def _write(repo: str, rel: str, text: str) -> None:
    path = Path(repo, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _kinds(issues) -> set[str]:
    return {i.kind for i in issues}


# ---------------------------------------------------------------------------
# validate_toc_merge — every blocking kind must fire positively
# ---------------------------------------------------------------------------


def test_pr_42884_collapsed_toc_when_en_shrunk_to_half():
    """#42884 / §6.44 / §6.63: fork EN collapsed to a handful of entries."""
    en_main = dedent("""
        items:
        - name: A
          href: a.md
        - name: B
          href: b.md
        - name: C
          href: c.md
        - name: D
          href: d.md
        - name: E
          href: e.md
        - name: F
          href: f.md
    """).strip()
    en_merged = dedent("""
        items:
        - name: A
          href: a.md
        - name: New
          href: new.md
    """).strip()
    ru = dedent("""
        items:
        - name: A
          href: a.md
        - name: New
          href: new.md
    """).strip()
    issues = validate_toc_merge(
        ru, en_merged, translate_hrefs={"new.md"}, en_main_yaml=en_main
    )
    assert "collapsed_toc" in _kinds(issues)


def test_pr_44872_unexpected_href_not_in_ru_or_en_main():
    """#44872-class: EN gained an href absent from RU PR and EN main."""
    en_main = dedent("""
        items:
        - name: Stable
          href: stable.md
    """).strip()
    en_merged = dedent("""
        items:
        - name: Stable
          href: stable.md
        - name: Hallucinated
          href: recipes/system-tablet-backup/index.md
    """).strip()
    ru = dedent("""
        items:
        - name: Stable
          href: stable.md
    """).strip()
    issues = validate_toc_merge(
        ru, en_merged, translate_hrefs=set(), en_main_yaml=en_main
    )
    assert "unexpected_href" in _kinds(issues)
    assert "toc_structure_parity" in _kinds(issues)
    assert any("system-tablet-backup" in i.detail for i in issues)


def test_pr_43753_toc_structure_parity_ru_en_menus_must_match():
    """§6.121: RU/EN toc href sets must match (orphan OTel recipes class)."""
    en_main = dedent("""
        items:
        - name: Overview
          href: debug.md
        - name: Tracing
          href: debug-otel.md
    """).strip()
    # EN re-added Troubleshooting with old href; RU already dropped the section.
    en_merged = en_main
    ru = dedent("""
        items:
        - name: Обзор
          href: index.md
    """).strip()
    issues = validate_toc_merge(
        ru, en_merged, translate_hrefs=set(), en_main_yaml=en_main
    )
    assert "toc_structure_parity" in _kinds(issues)
    # EN-only that already sat on main → soft drift warning, not parity block alone
    legacy = [i for i in issues if i.kind == "toc_en_only_legacy"]
    assert legacy
    assert "debug-otel.md" in legacy[0].detail


def test_toc_en_only_legacy_when_preserved_from_main():
    ru = dedent("""
        items:
        - name: A
          href: a.md
    """).strip()
    en = dedent("""
        items:
        - name: A
          href: a.md
        - name: EN only
          href: local-and-external-topics.md
    """).strip()
    issues = validate_toc_merge(
        ru, en, translate_hrefs={"a.md"}, en_main_yaml=en
    )
    kinds = _kinds(issues)
    assert "toc_en_only_legacy" in kinds
    assert "toc_structure_parity" not in kinds
    assert "unexpected_href" not in kinds


def test_pr_42725_empty_toc_when_parse_yields_no_items():
    """#42725 / §6.33: broken inline toc parsed as empty → empty_toc."""
    en_main = "items:\n"
    en_merged = "items:\n"
    ru = dedent("""
        items:
        - name: Page
          href: page.md
    """).strip()
    issues = validate_toc_merge(
        ru, en_merged, translate_hrefs={"page.md"}, en_main_yaml=en_main
    )
    assert "empty_toc" in _kinds(issues)


def test_pr_42726_inconsistent_indent_mixed_inline_prefixes():
    """#42726 / §6.34: mixed ``- {`` indent prefixes → inconsistent_indent."""
    en_main = dedent("""
        items:
          - { name: A, href: a.md }
          - { name: B, href: b.md }
    """).strip()
    # One entry uses 0-space list prefix, another uses 2-space (invalid mix).
    en_merged = (
        "items:\n"
        "- { name: A, href: a.md }\n"
        "  - { name: B, href: b.md }\n"
    )
    ru = en_merged
    issues = validate_toc_merge(
        ru, en_merged, translate_hrefs=set(), en_main_yaml=en_main
    )
    assert "inconsistent_indent" in _kinds(issues)


def test_pr_44942_scope_not_applied_when_href_missing_from_en():
    """#44942 / §6.74: scoped href never landed in EN toc."""
    en_main = dedent("""
        items:
        - name: Hive
          href: hive.md
    """).strip()
    en_merged = en_main
    ru = dedent("""
        items:
        - name: Hive
          href: hive.md
        - name: New page
          href: new-config.md
    """).strip()
    issues = validate_toc_merge(
        ru,
        en_merged,
        translate_hrefs={"new-config.md"},
        en_main_yaml=en_main,
    )
    assert "scope_not_applied" in _kinds(issues)
    assert any("new-config.md" in i.detail for i in issues)


def test_pr_47100_scope_not_applied_false_positive_href_plus_include():
    """#47100 / §6.118: Spring href+include must satisfy include.path scope."""
    en = dedent("""
        items:
        - name: Spring
          href: spring/index.md
          include:
            mode: link
            path: spring/toc-spring.yaml
        - name: Vector search
          href: vectorsearch/index.md
    """).strip()
    ru = en
    issues = validate_toc_merge(
        ru,
        en,
        translate_hrefs={"spring/index.md"},
        translate_include_paths={"spring/toc-spring.yaml"},
        en_main_yaml=dedent("""
            items:
            - name: Vector search
              href: vectorsearch/index.md
        """).strip(),
    )
    assert "scope_not_applied" not in _kinds(issues)


# ---------------------------------------------------------------------------
# merge_en_toc_yaml — production merge contracts
# ---------------------------------------------------------------------------


def test_pr_47100_merge_preserves_href_and_include_on_spring_section():
    """#47100 / #43010: merging Spring into integrations toc keeps both fields."""
    en_main = dedent("""
        items:
        - name: ORM
          href: orm/index.md
          include:
            mode: link
            path: orm/toc-orm.yaml
        - name: Vector search
          href: vectorsearch/index.md
          include:
            mode: link
            path: vectorsearch/toc-vectorsearch.yaml
        - name: SQL Dialect Converter to YQL
          href: sql-dialect-converter.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: ORM
          href: orm/index.md
          include:
            mode: link
            path: orm/toc-orm.yaml
        - name: Spring
          href: spring/index.md
          include:
            mode: link
            path: spring/toc-spring.yaml
        - name: Vector search
          href: vectorsearch/index.md
          include:
            mode: link
            path: vectorsearch/toc-vectorsearch.yaml
        - name: SQL translation
          href: sql-translation/index.md
          include:
            mode: link
            path: sql-translation/toc-sql-translation.yaml
    """).strip()
    scope = toc_translate_scope(
        dedent("""
            items:
            - name: ORM
              href: orm/index.md
              include:
                mode: link
                path: orm/toc-orm.yaml
            - name: Vector search
              href: vectorsearch/index.md
              include:
                mode: link
                path: vectorsearch/toc-vectorsearch.yaml
            - name: SQL translation
              href: sql-translation/index.md
              include:
                mode: link
                path: sql-translation/toc-sql-translation.yaml
        """).strip(),
        ru_pr,
    )
    assert scope.hrefs == {"spring/index.md"}
    assert scope.include_paths == {"spring/toc-spring.yaml"}

    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs=set(scope.hrefs),
        translate_include_paths=set(scope.include_paths),
        translate_name=lambda n: n if n != "Spring" else "Spring",
        restrict_gap_fill_to_scope=True,
    )
    items = parse_toc_items(merged)
    spring = next(it for it in items if it.get("href") == "spring/index.md")
    assert spring["include_path"] == "spring/toc-spring.yaml"
    assert "sql-dialect-converter.md" in {it.get("href") for it in items}
    assert "sql-translation/toc-sql-translation.yaml" not in merged


def test_pr_46349_absent_en_toc_full_mirror_from_ru():
    """#46349 / §6.85: empty EN sidebar → full RU mirror for that toc."""
    ru = dedent("""
        items:
        - name: Overview
          href: index.md
        - name: Auth
          href: auth.md
        - include: { mode: link, path: toc_i.yaml }
    """).strip()
    merged = merge_en_toc_yaml(
        "",
        ru,
        translate_hrefs={"index.md", "auth.md"},
        translate_include_paths={"toc_i.yaml"},
        translate_name=lambda n: {"Overview": "Overview", "Auth": "Auth"}.get(n, n),
    )
    items = parse_toc_items(merged)
    assert {it.get("href") for it in items if it.get("href")} == {"index.md", "auth.md"}
    assert any(it.get("include_path") == "toc_i.yaml" for it in items)


def test_pr_44916_supplement_only_does_not_gap_fill_unrelated_ru_base():
    """#44916 / §6.72: parent queued from main must not pull hive/kafka."""
    en_main = dedent("""
        items:
        - name: Topic
          href: topic.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: Topic
          href: topic.md
        - name: Diagnostics
          href: diagnostics.md
        - name: Hive
          href: hive.md
        - name: Kafka
          href: kafka.md
    """).strip()
    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs={"diagnostics.md"},
        translate_name=lambda n: n,
        ru_base_hrefs={"topic.md", "diagnostics.md", "hive.md", "kafka.md"},
        restrict_gap_fill_to_scope=True,
    )
    hrefs = {it["href"] for it in parse_toc_items(merged) if it.get("href")}
    assert "diagnostics.md" in hrefs
    assert "topic.md" in hrefs
    assert "hive.md" not in hrefs
    assert "kafka.md" not in hrefs


# ---------------------------------------------------------------------------
# scope planner — md-only / parent-queue / multi-section (#46569 family)
# ---------------------------------------------------------------------------


def test_pr_44889_md_only_queues_parent_toc_when_en_missing_href():
    """#44889 / §6.71: page added under section; EN parent toc lacks the href."""
    files = {
        "ydb/docs/ru/core/recipes/toc_p.yaml": dedent("""
            items:
            - name: Backup
              href: system-tablet-backup/index.md
            - name: JSON search
              href: json-search/index.md
              include:
                mode: link
                path: json-search/toc_p.yaml
        """).strip(),
        "ydb/docs/ru/core/recipes/json-search/index.md": "# JSON\n",
        "ydb/docs/ru/core/recipes/json-search/toc_p.yaml": dedent("""
            items:
            - name: Overview
              href: index.md
            - name: Quickstart
              href: json-index-quickstart.md
        """).strip(),
        "ydb/docs/ru/core/recipes/json-search/json-index-quickstart.md": "# QS\n",
        "ydb/docs/en/core/recipes/toc_p.yaml": dedent("""
            items:
            - name: Backup
              href: system-tablet-backup/index.md
        """).strip(),
    }

    def read_ru(path: str) -> str | None:
        return files.get(path)

    def read_en(path: str) -> str | None:
        return files.get(path) if path.startswith("ydb/docs/en/") else None

    plan = plan_translation_scope(
        [
            ("ydb/docs/ru/core/recipes/json-search/index.md", "added"),
            ("ydb/docs/ru/core/recipes/json-search/json-index-quickstart.md", "added"),
            ("ydb/docs/ru/core/recipes/json-search/toc_p.yaml", "added"),
        ],
        read_ru=read_ru,
        read_en_base=read_en,
        read_ru_base=lambda _p: None,
    )
    assert "ydb/docs/ru/core/recipes/toc_p.yaml" in plan.nav_ru_paths
    assert "ydb/docs/ru/core/recipes/json-search/toc_p.yaml" in plan.nav_ru_paths


def test_pr_46569_queues_all_three_parent_tocs_for_mixed_sections():
    """#46569: streaming + json-search + sql-translation need three parents."""
    files = {
        "ydb/docs/ru/core/concepts/toc_i.yaml": dedent("""
            items:
            - name: Streaming
              href: streaming-query/index.md
              include:
                mode: link
                path: streaming-query/toc_p.yaml
        """).strip(),
        "ydb/docs/ru/core/concepts/streaming-query/toc_p.yaml": (
            "items:\n- name: Overview\n  href: streaming-query.md\n"
        ),
        "ydb/docs/ru/core/concepts/streaming-query/streaming-query.md": "# SQ\n",
        "ydb/docs/ru/core/concepts/streaming-query/index.md": "# idx\n",
        "ydb/docs/en/core/concepts/toc_i.yaml": (
            "items:\n- name: Streaming\n  href: streaming-query.md\n"
        ),
        "ydb/docs/ru/core/recipes/toc_p.yaml": dedent("""
            items:
            - name: JSON search
              href: json-search/index.md
              include:
                mode: link
                path: json-search/toc_p.yaml
        """).strip(),
        "ydb/docs/ru/core/recipes/json-search/toc_p.yaml": (
            "items:\n- name: Overview\n  href: index.md\n"
        ),
        "ydb/docs/ru/core/recipes/json-search/index.md": "# J\n",
        "ydb/docs/en/core/recipes/toc_p.yaml": (
            "items:\n- name: Backup\n  href: backup.md\n"
        ),
        "ydb/docs/ru/core/integrations/toc_i.yaml": dedent("""
            items:
            - name: SQL translation
              href: sql-translation/index.md
              include:
                mode: link
                path: sql-translation/toc-sql-translation.yaml
        """).strip(),
        "ydb/docs/ru/core/integrations/sql-translation/toc-sql-translation.yaml": (
            "items:\n- name: Overview\n  href: index.md\n"
        ),
        "ydb/docs/ru/core/integrations/sql-translation/index.md": "# S\n",
        "ydb/docs/en/core/integrations/toc_i.yaml": (
            "items:\n- name: Converter\n  href: sql-dialect-converter.md\n"
        ),
    }

    def read_ru(path: str) -> str | None:
        return files.get(path)

    def read_en(path: str) -> str | None:
        return files.get(path) if "/en/" in path else None

    plan = plan_translation_scope(
        [
            ("ydb/docs/ru/core/concepts/streaming-query/streaming-query.md", "added"),
            ("ydb/docs/ru/core/concepts/streaming-query/toc_p.yaml", "added"),
            ("ydb/docs/ru/core/recipes/json-search/index.md", "added"),
            ("ydb/docs/ru/core/recipes/json-search/toc_p.yaml", "added"),
            ("ydb/docs/ru/core/integrations/sql-translation/index.md", "added"),
            (
                "ydb/docs/ru/core/integrations/sql-translation/"
                "toc-sql-translation.yaml",
                "added",
            ),
        ],
        read_ru=read_ru,
        read_en_base=read_en,
        read_ru_base=lambda _p: None,
    )
    assert "ydb/docs/ru/core/concepts/toc_i.yaml" in plan.nav_ru_paths
    assert "ydb/docs/ru/core/recipes/toc_p.yaml" in plan.nav_ru_paths
    assert "ydb/docs/ru/core/integrations/toc_i.yaml" in plan.nav_ru_paths


def test_pr_43010_spring_queues_integrations_parent_and_child_toc():
    """#43010 → #47100: Spring section needs integrations/toc_i + toc-spring."""
    files = {
        "ydb/docs/ru/core/integrations/toc_i.yaml": dedent("""
            items:
            - name: ORM
              href: orm/index.md
              include:
                mode: link
                path: orm/toc-orm.yaml
            - name: Spring
              href: spring/index.md
              include:
                mode: link
                path: spring/toc-spring.yaml
            - name: Vector search
              href: vectorsearch/index.md
              include:
                mode: link
                path: vectorsearch/toc-vectorsearch.yaml
        """).strip(),
        "ydb/docs/ru/core/integrations/spring/toc-spring.yaml": (
            "items:\n- name: Retry\n  href: spring-retry.md\n"
        ),
        "ydb/docs/ru/core/integrations/spring/index.md": "# Spring\n",
        "ydb/docs/ru/core/integrations/spring/spring-retry.md": "# Retry\n",
        "ydb/docs/en/core/integrations/toc_i.yaml": dedent("""
            items:
            - name: ORM
              href: orm/index.md
              include:
                mode: link
                path: orm/toc-orm.yaml
            - name: Vector search
              href: vectorsearch/index.md
              include:
                mode: link
                path: vectorsearch/toc-vectorsearch.yaml
            - name: SQL Dialect Converter to YQL
              href: sql-dialect-converter.md
        """).strip(),
    }

    def read_ru(path: str) -> str | None:
        return files.get(path)

    def read_en(path: str) -> str | None:
        return files.get(path) if "/en/" in path else None

    plan = plan_translation_scope(
        [
            ("ydb/docs/ru/core/integrations/spring/index.md", "added"),
            ("ydb/docs/ru/core/integrations/spring/spring-retry.md", "added"),
            ("ydb/docs/ru/core/integrations/spring/toc-spring.yaml", "added"),
            ("ydb/docs/ru/core/integrations/toc_i.yaml", "modified"),
        ],
        read_ru=read_ru,
        read_en_base=read_en,
        read_ru_base=lambda p: (
            files["ydb/docs/en/core/integrations/toc_i.yaml"]
            if p == "ydb/docs/ru/core/integrations/toc_i.yaml"
            else None
        ),
    )
    assert "ydb/docs/ru/core/integrations/toc_i.yaml" in plan.nav_ru_paths
    assert "ydb/docs/ru/core/integrations/spring/toc-spring.yaml" in plan.nav_ru_paths
    assert {
        "ydb/docs/ru/core/integrations/spring/index.md",
        "ydb/docs/ru/core/integrations/spring/spring-retry.md",
    } <= plan.doc_ru_paths


def test_pr_46338_queues_child_toc_via_parent_include_path():
    """#46338 / §6.84: parent already lists page; child toc still queued via include."""
    files = {
        "ydb/docs/ru/core/reference/toc_p.yaml": dedent("""
            items:
            - name: SQS API
              href: sqs-api/index.md
              include:
                mode: link
                path: sqs-api/toc_p.yaml
        """).strip(),
        "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml": (
            "items:\n- include: { mode: link, path: toc_i.yaml }\n"
        ),
        "ydb/docs/ru/core/reference/sqs-api/toc_i.yaml": (
            "items:\n- name: Overview\n  href: index.md\n"
            "- name: Auth\n  href: auth.md\n"
        ),
        "ydb/docs/ru/core/reference/sqs-api/index.md": "# idx\n",
        "ydb/docs/ru/core/reference/sqs-api/auth.md": "# auth\n",
        "ydb/docs/en/core/reference/toc_p.yaml": dedent("""
            items:
            - name: SQS API
              href: sqs-api/index.md
              include:
                mode: link
                path: sqs-api/toc_p.yaml
        """).strip(),
    }

    def read_ru(path: str) -> str | None:
        return files.get(path)

    def read_en(path: str) -> str | None:
        if path == "ydb/docs/en/core/reference/toc_p.yaml":
            return files[path]
        # EN child tocs absent → must be queued
        return None

    plan = plan_translation_scope(
        [
            ("ydb/docs/ru/core/reference/sqs-api/auth.md", "added"),
            ("ydb/docs/ru/core/reference/sqs-api/toc_i.yaml", "added"),
        ],
        read_ru=read_ru,
        read_en_base=read_en,
        read_ru_base=lambda _p: None,
    )
    assert "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml" in plan.nav_ru_paths
    assert "ydb/docs/ru/core/reference/sqs-api/toc_i.yaml" in plan.nav_ru_paths


def test_pr_include_closure_queues_locale_include_not_in_diff():
    """§22.4 step 4 / §6.90: ``{% include %}`` pulled even when not in PR list."""
    files = {
        "ydb/docs/ru/core/integrations/spring/index.md": (
            "# Spring\n\n"
            "{% include [table](_includes/toc-table.md) %}\n"
        ),
        "ydb/docs/ru/core/integrations/spring/_includes/toc-table.md": "| a | b |\n",
        "ydb/docs/ru/core/integrations/spring/toc-spring.yaml": (
            "items:\n- name: Overview\n  href: index.md\n"
        ),
        "ydb/docs/ru/core/integrations/toc_i.yaml": dedent("""
            items:
            - name: Spring
              href: spring/index.md
              include:
                mode: link
                path: spring/toc-spring.yaml
        """).strip(),
        "ydb/docs/en/core/integrations/toc_i.yaml": "items:\n",
    }

    def read_ru(path: str) -> str | None:
        return files.get(path)

    def read_en(path: str) -> str | None:
        return files.get(path) if "/en/" in path else None

    plan = plan_translation_scope(
        [("ydb/docs/ru/core/integrations/spring/index.md", "added")],
        read_ru=read_ru,
        read_en_base=read_en,
        read_ru_base=lambda _p: None,
    )
    assert (
        "ydb/docs/ru/core/integrations/spring/_includes/toc-table.md"
        in plan.doc_ru_paths
    )


# ---------------------------------------------------------------------------
# QA heuristics — missing targets + orphans (#46338 / #46569)
# ---------------------------------------------------------------------------


def test_pr_46878_supplement_only_does_not_add_all_missing_ru_hrefs():
    """#46878: parent concepts/toc_i queued for query_execution must not add
    secondary_indexes.md / architecture.md (absent on EN).
    """
    from ydbdoc_review.pipeline.navigation_merge import _resolve_toc_merge_scope
    from ydbdoc_review.pipeline.pairs import NavigationPair

    en_main = dedent("""
        items:
        - name: Glossary
          href: glossary.md
        - name: Architecture
          href: architecture/index.md
          include:
            path: architecture/toc_p.yaml
            mode: link
        - name: Query execution
          href: query_execution/index.md
          include:
            path: query_execution/toc_p.yaml
            mode: link
        - name: Streaming queries
          href: streaming-query.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: Glossary
          href: glossary.md
        - name: Architecture
          href: architecture/index.md
          include:
            path: architecture/toc_p.yaml
            mode: link
        - name: Query execution
          href: query_execution/index.md
          include:
            path: query_execution/toc_p.yaml
            mode: link
        - name: Secondary indexes
          href: secondary_indexes.md
        - name: Streaming queries
          href: streaming-query/index.md
          include:
            path: streaming-query/toc_p.yaml
            mode: link
    """).strip()
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/concepts/toc_i.yaml",
        en_path="ydb/docs/en/core/concepts/toc_i.yaml",
        ru_changed=False,
        supplement_only=True,
    )
    scope, restrict = _resolve_toc_merge_scope(
        pair,
        ru_base=ru_pr,
        ru_pr=ru_pr,
        en_main=en_main,
        pair_extra_hrefs={"query_execution/index.md"},
        pair_extra_includes={"query_execution/toc_p.yaml"},
    )
    assert restrict is True
    assert "secondary_indexes.md" not in scope.hrefs
    assert "streaming-query/index.md" not in scope.hrefs
    assert "query_execution/index.md" in scope.hrefs
    assert "query_execution/toc_p.yaml" in scope.include_paths

    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs=set(scope.hrefs),
        translate_include_paths=set(scope.include_paths),
        translate_name=lambda n: n,
        restrict_gap_fill_to_scope=True,
    )
    hrefs = {it.get("href") for it in parse_toc_items(merged) if it.get("href")}
    assert "secondary_indexes.md" not in hrefs
    assert "architecture.md" not in hrefs
    assert "architecture/index.md" in hrefs
    assert "streaming-query.md" in hrefs
    assert "query_execution/index.md" in hrefs


def test_pr_46338_missing_toc_target_for_absent_include_yaml(tmp_path: Path):
    """#46338 / §6.83: EN toc include.path → missing child yaml on disk."""
    repo = _repo(tmp_path)
    en_toc = "ydb/docs/en/core/integrations/toc_i.yaml"
    toc = dedent("""
        items:
        - name: SQL translation
          href: sql-translation/index.md
          include:
            mode: link
            path: sql-translation/toc-sql-translation.yaml
    """).strip()
    msgs = check_missing_toc_targets(en_toc, toc, repo_path=repo)
    assert any(m.startswith("missing_toc_target:") for m in msgs)
    assert any("toc-sql-translation.yaml" in m for m in msgs)


def test_pr_46569_orphan_page_when_parent_not_wired(tmp_path: Path):
    """#46569 / §6.117: child toc pending but disconnected from root → orphan."""
    repo = _repo(tmp_path)
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Concepts\n  include: { mode: link, path: concepts/toc_i.yaml }\n",
    )
    _write(
        repo,
        "ydb/docs/en/core/concepts/toc_i.yaml",
        "items:\n- name: Overview\n  href: index.md\n",
    )
    page = "ydb/docs/en/core/concepts/streaming-query/watermarks.md"
    orphans = check_orphan_translated_pages(
        {page},
        repo_path=repo,
        docs_root="ydb/docs",
        pending_toc_texts={
            "ydb/docs/en/core/concepts/streaming-query/toc_p.yaml": (
                "items:\n- name: Watermarks\n  href: watermarks.md\n"
            ),
        },
    )
    assert page in orphans
