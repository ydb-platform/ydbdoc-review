"""Tests for navigation merge in doc_translate pipeline."""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.navigation.scope_planner import TranslationScopePlan
from ydbdoc_review.pipeline.navigation_merge import (
    extra_toc_hrefs_for_pair,
    extra_toc_hrefs_from_md_targets,
    merge_navigation_pair,
    verify_navigation_pair,
)
from ydbdoc_review.pipeline.pairs import NavigationPair
from ydbdoc_review.translation.glossary import load_glossary

RU_BASE = dedent("""
    items:
    - { name: Обзор,      href: index.md                                          }
    - { name: FAMILY,     href: family.md,          when: backend_name == "YDB"   }
""").strip()

RU_PR = dedent("""
    items:
    - { name: Обзор,      href: index.md                                          }
    - { name: FAMILY,     href: family.md,          when: backend_name == "YDB"   }
    - { name: COMPACT,    href: compact.md,         when: backend_name == "YDB"   }
""").strip()

EN_MAIN = dedent("""
    items:
     - { name: Overview,    href: index.md                                          }
     - { name: FAMILY,      href: family.md                                         }
""").strip()


def test_merge_navigation_pair_inline_toc():
    client = MagicMock()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    glossary = load_glossary()
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/alter_table/toc_i.yaml",
        en_path="ydb/docs/en/core/alter_table/toc_i.yaml",
        ru_changed=True,
    )

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=RU_PR,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            return_value=(RU_BASE, EN_MAIN),
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={"COMPACT": "COMPACT"},
        ),
    ):
        result = merge_navigation_pair(
            pair,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=cfg,
            extra_toc_hrefs={"compact.md"},
        )

    assert result.error is None
    assert result.target_text is not None
    assert result.verdict == "ok"
    assert "compact.md" in result.target_text
    assert "COMPACT" in result.target_text
    assert "Overview" in result.target_text
    assert "FAMILY" in result.target_text
    for line in result.target_text.splitlines():
        if line.strip().startswith("- {"):
            assert line.startswith(" - {"), line


def test_extra_toc_hrefs_from_md_targets_skips_locale_includes():
    paths = {
        "ydb/docs/en/core/integrations/orm/exposed.md",
        "ydb/docs/en/core/integrations/orm/_includes/toc-table.md",
    }
    assert extra_toc_hrefs_from_md_targets(paths) == {"exposed.md"}


ORM_RU_BASE = dedent("""
    items:
    - { name: Hibernate, href: hibernate.md }
    - { name: Django, href: django.md }
""").strip()

ORM_RU_PR = dedent("""
    items:
    - { name: Hibernate, href: hibernate.md }
    - { name: Django, href: django.md }
    - { name: Kotlin Exposed, href: exposed.md }
""").strip()

ORM_EN_MAIN = ORM_RU_BASE
ORM_EN_OK = ORM_RU_PR


def test_verify_orm_toc_ok_when_include_translated_not_in_sidebar():
    """Regression: PR #42768 — toc-table.md must not be required in toc-orm.yaml."""
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/integrations/orm/toc-orm.yaml",
        en_path="ydb/docs/en/core/integrations/orm/toc-orm.yaml",
        en_changed=True,
    )
    md_paths = {
        "ydb/docs/en/core/integrations/orm/exposed.md",
        "ydb/docs/en/core/integrations/orm/_includes/toc-table.md",
    }
    result = verify_navigation_pair(
        pair,
        ru_pr=ORM_RU_PR,
        en_text=ORM_EN_OK,
        ru_base=ORM_RU_BASE,
        en_main=ORM_EN_MAIN,
        extra_toc_hrefs=extra_toc_hrefs_from_md_targets(md_paths),
    )
    assert result.verdict == "ok"
    assert not any("toc-table.md" in w for w in result.warnings)


STREAMING_EN_MAIN = dedent("""
    items:
    - name: Local and external topics
      href: local-and-external-topics.md
    - name: Common patterns
      href: patterns.md
    - name: Writing to tables
      href: table-writing.md
    - name: Data enrichment
      href: enrichment.md
    - name: Topic read and write formats
      href: streaming-query-formats.md
    - name: Delivery guarantees
      href: guarantees.md
    - name: Checkpoints
      href: checkpoints.md
""").strip()

STREAMING_RU_PR = dedent("""
    items:
    - name: Типичные шаблоны
      href: patterns.md
    - name: Запись в таблицы
      href: table-writing.md
    - name: Обогащение данных
      href: enrichment.md
    - name: Форматы данных при чтении/записи топиков
      href: streaming-query-formats.md
    - name: Гарантии доставки данных
      href: guarantees.md
    - name: Чекпоинты
      href: checkpoints.md
""").strip()

STREAMING_RU_BASE_STALE = dedent("""
    items:
    - name: Типичные шаблоны
      href: patterns.md
    - name: Запись в таблицы
      href: table-writing.md
    - name: Обогащение данных
      href: enrichment.md
    - name: Форматы данных при чтении/записи топиков
      href: streaming-query-formats.md
    - name: Гарантии доставки данных
      href: guarantees.md
    - name: Чекпоинты
      href: checkpoints.md
""").strip()

STREAMING_EN_AT_MERGE_BASE_STALE = dedent("""
    items:
    - name: Common patterns
      href: patterns.md
    - name: Writing to tables
      href: table-writing.md
    - name: Data enrichment
      href: enrichment.md
    - name: Topic read and write formats
      href: streaming-query-formats.md
    - name: Delivery guarantees
      href: guarantees.md
    - name: Checkpoints
      href: checkpoints.md
""").strip()


def test_read_navigation_baselines_prefers_upstream_en_main():
    """§6.111 / #46845: EN baseline is current main, not stale PR merge-base."""
    from ydbdoc_review.pipeline.navigation_merge import _read_navigation_baselines

    def fake_read(_repo: str, ref: str, path: str) -> str | None:
        if path.endswith("toc_i.yaml") and "en/" in path:
            if ref in {"origin/main", "main"}:
                return STREAMING_EN_MAIN
            return STREAMING_EN_AT_MERGE_BASE_STALE
        if path.endswith("toc_i.yaml") and "ru/" in path:
            return STREAMING_RU_BASE_STALE
        return None

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.merge_base",
            return_value="merge-base-sha",
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text_at_ref",
            side_effect=fake_read,
        ),
    ):
        ru_base, en_main = _read_navigation_baselines(
            "/tmp/repo",
            "origin/main",
            ru_path="ydb/docs/ru/core/dev/streaming-query/toc_i.yaml",
            en_path="ydb/docs/en/core/dev/streaming-query/toc_i.yaml",
        )

    assert "local-and-external-topics.md" in en_main
    assert "local-and-external-topics.md" not in STREAMING_EN_AT_MERGE_BASE_STALE
    assert ru_base == STREAMING_RU_BASE_STALE


def test_merge_preserves_en_only_href_present_on_current_main():
    """Regression #46845: do not drop EN-only toc entries when merging RU toc."""
    client = MagicMock()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    glossary = load_glossary()
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/dev/streaming-query/toc_i.yaml",
        en_path="ydb/docs/en/core/dev/streaming-query/toc_i.yaml",
        ru_changed=True,
    )

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=STREAMING_RU_PR,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            return_value=(STREAMING_RU_BASE_STALE, STREAMING_EN_MAIN),
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={"Обогащение данных": "Data enrichment"},
        ),
    ):
        result = merge_navigation_pair(
            pair,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=cfg,
            extra_toc_hrefs={"enrichment.md"},
        )

    assert result.verdict in {"ok", "warnings"}  # toc_en_only_legacy soft drift (§6.121)
    assert result.target_text is not None
    assert "local-and-external-topics.md" in result.target_text
    assert "patterns.md" in result.target_text
    assert "checkpoints.md" in result.target_text


def test_merge_fork_pr_toc_uses_upstream_en_main_fallback():
    """Regression: PR #42884 — fork checkout has RU toc but no EN toc at merge-base."""
    client = MagicMock()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    glossary = load_glossary()
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/dev/streaming-query/toc_i.yaml",
        en_path="ydb/docs/en/core/dev/streaming-query/toc_i.yaml",
        ru_changed=True,
    )
    md_basenames = extra_toc_hrefs_from_md_targets(
        {
            "ydb/docs/en/core/dev/streaming-query/enrichment.md",
            "ydb/docs/en/core/dev/streaming-query/index.md",
            "ydb/docs/en/core/recipes/streaming_queries/topics.md",
        }
    )
    assert extra_toc_hrefs_for_pair(STREAMING_RU_PR, md_basenames) == {"enrichment.md"}

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=STREAMING_RU_PR,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            return_value=(STREAMING_RU_PR, STREAMING_EN_MAIN),
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={"Обогащение данных": "Data enrichment"},
        ),
    ):
        result = merge_navigation_pair(
            pair,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=cfg,
            extra_toc_hrefs=md_basenames,
        )

    assert result.verdict in {"ok", "warnings"}  # toc_en_only_legacy soft drift (§6.121)
    assert result.target_text is not None
    assert "patterns.md" in result.target_text
    assert "checkpoints.md" in result.target_text
    assert "local-and-external-topics.md" in result.target_text
    hrefs = [
        line.split("href:", 1)[1].strip()
        for line in result.target_text.splitlines()
        if "href:" in line
    ]
    assert "topics.md" not in hrefs


OBSERVABILITY_RU_TOC = dedent("""
    items:
    - name: Обзор
      href: index.md
    - name: Логирование
      include:
        mode: link
        path: logging/toc_p.yaml
    - name: Метрики
      include:
        mode: link
        path: metrics/toc_p.yaml
""").strip()


def test_extra_toc_hrefs_for_pair_skips_include_only_entries():
    """Regression #44103: include.path items must not raise KeyError on href."""
    md_basenames = {"index.md", "logging.md", "opentelemetry.md"}
    assert extra_toc_hrefs_for_pair(OBSERVABILITY_RU_TOC, md_basenames) == {"index.md"}


SQS_RU_TOC_P = dedent("""
    items:
    - name: Обзор
      href: index.md
    - include: { mode: link, path: toc_i.yaml }
""").strip()


def test_merge_navigation_pair_mirrors_absent_en_toc_from_ru():
    """Regression #46349: absent EN toc must mirror RU, not emit empty items."""
    client = MagicMock()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    glossary = load_glossary()
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/reference/sqs-api/toc_p.yaml",
        en_path="ydb/docs/en/core/reference/sqs-api/toc_p.yaml",
        ru_changed=True,
        supplement_only=True,
    )

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=SQS_RU_TOC_P,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            return_value=(SQS_RU_TOC_P, ""),
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={"Обзор": "Overview"},
        ),
    ):
        result = merge_navigation_pair(
            pair,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=cfg,
            extra_toc_hrefs={"index.md"},
        )

    assert result.verdict == "ok"
    assert result.target_text is not None
    assert "index.md" in result.target_text
    assert "toc_i.yaml" in result.target_text
    assert "Overview" in result.target_text
    assert result.target_text.strip() != "items:"


def test_merge_navigation_pair_uses_scope_plan_for_toc_extras():
    """J.6: scope plan replaces extra_toc_hrefs for translate merge."""
    client = MagicMock()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    glossary = load_glossary()
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/reference/sqs-api/toc_p.yaml",
        en_path="ydb/docs/en/core/reference/sqs-api/toc_p.yaml",
        ru_changed=True,
        supplement_only=True,
    )
    scope_plan = TranslationScopePlan(
        doc_ru_paths=frozenset(
            {
                "ydb/docs/ru/core/reference/sqs-api/index.md",
                "ydb/docs/ru/core/reference/sqs-api/auth.md",
            }
        ),
        doc_from_diff=frozenset(),
        doc_from_main=frozenset(
            {
                "ydb/docs/ru/core/reference/sqs-api/index.md",
                "ydb/docs/ru/core/reference/sqs-api/auth.md",
            }
        ),
        nav_ru_paths=frozenset(
            {
                "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml",
                "ydb/docs/ru/core/reference/sqs-api/toc_i.yaml",
            }
        ),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(
            {
                "ydb/docs/ru/core/reference/sqs-api/toc_p.yaml",
                "ydb/docs/ru/core/reference/sqs-api/toc_i.yaml",
            }
        ),
    )

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=SQS_RU_TOC_P,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            return_value=(SQS_RU_TOC_P, ""),
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={"Обзор": "Overview"},
        ),
    ):
        result = merge_navigation_pair(
            pair,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=cfg,
            scope_plan=scope_plan,
        )

    assert result.verdict == "ok"
    assert result.target_text is not None
    assert "index.md" in result.target_text
    assert "toc_i.yaml" in result.target_text
    assert "auth.md" not in result.target_text

