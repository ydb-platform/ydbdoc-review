"""Tests for toc.yaml scoped merge."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.toc import (
    merge_en_toc_yaml,
    parse_toc_items,
    toc_translate_scope,
    validate_toc_merge,
)

RU_BASE = dedent("""
    items:
    - name: Старый раздел
      href: old.md
    - name: Без изменений
      href: stable.md
""").strip()

RU_PR = dedent("""
    items:
    - name: Старый раздел (переименован)
      href: old.md
    - name: Без изменений
      href: stable.md
    - name: Новый раздел
      href: new-page.md
""").strip()

EN_MAIN = dedent("""
    items:
    - name: Old section
      href: old.md
    - name: Unchanged
      href: stable.md
    - name: EN legacy only
      href: legacy.md
""").strip()


def test_parse_toc_items():
    items = parse_toc_items(RU_PR)
    assert len(items) == 3
    assert items[0]["href"] == "old.md"
    assert items[2]["name"] == "Новый раздел"


def test_toc_translate_scope_detects_new_and_renamed():
    scope = toc_translate_scope(RU_BASE, RU_PR)
    assert scope.hrefs == {"old.md", "new-page.md"}
    assert "stable.md" not in scope.hrefs


def test_merge_keeps_unchanged_en_labels():
    scope = toc_translate_scope(RU_BASE, RU_PR)

    def fake_translate(name: str) -> str:
        return {"Старый раздел (переименован)": "Old section (renamed)", "Новый раздел": "New section"}[
            name
        ]

    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs=set(scope.hrefs),
        translate_name=fake_translate,
    )
    items = parse_toc_items(merged)
    by_href = {it["href"]: it["name"] for it in items}

    assert by_href["stable.md"] == "Unchanged"
    assert by_href["old.md"] == "Old section (renamed)"
    assert by_href["new-page.md"] == "New section"
    assert by_href["legacy.md"] == "EN legacy only"


def test_merge_adds_ru_base_href_missing_from_en_main():
    """§6.59 #43365: pre-existing RU toc entry missing from EN main is added."""
    en_main = dedent("""
        items:
        - name: Troubleshooting
          items:
          - name: Enable logging
            href: debug-logs.md
          - name: Metrics with OpenTelemetry
            href: debug-otel-metrics.md
    """).strip()
    ru_base = dedent("""
        items:
        - name: Диагностика
          items:
          - name: Логирование
            href: debug-logs.md
          - name: OTel logs
            href: debug-logs-otel.md
          - name: Метрики OTel
            href: debug-otel-metrics.md
    """).strip()
    merged = merge_en_toc_yaml(
        en_main,
        ru_base,
        translate_hrefs={"debug-otel-metrics.md"},
        translate_name=lambda n: {
            "OTel logs": "Export logs to OpenTelemetry",
        }.get(n, n),
        ru_base_hrefs={"debug-logs.md", "debug-logs-otel.md", "debug-otel-metrics.md"},
    )
    hrefs = {it["href"] for it in parse_toc_items(merged)}
    assert "debug-logs-otel.md" in hrefs


def test_parse_toc_items_reads_indented_list_href():
    """Regression #46346: items indented under ``items:`` with 4-space ``href:``."""
    toc = dedent("""
        items:
          - name: Auth
            href: auth.md
          - name: Examples
            href: examples.md
    """).strip()
    items = parse_toc_items(toc)
    assert [it.get("href") for it in items] == ["auth.md", "examples.md"]


def test_merge_en_toc_mirrors_indented_absent_en_toc_i():
    """Regression #46346: sqs-api toc_i full mirror when EN absent."""
    ru = dedent("""
        items:
          - name: Аутентификация
            href: auth.md
          - name: Примеры
            href: examples.md
    """).strip()
    merged = merge_en_toc_yaml(
        "",
        ru,
        translate_hrefs={"auth.md", "examples.md"},
        translate_name=lambda n: {"Аутентификация": "Auth", "Примеры": "Examples"}.get(n, n),
        restrict_gap_fill_to_scope=False,
    )
    assert "auth.md" in merged
    assert "examples.md" in merged
    issues = validate_toc_merge(ru, merged, translate_hrefs={"auth.md", "examples.md"}, en_main_yaml="")
    assert not any(issue.kind == "empty_toc" for issue in issues)


def test_validate_toc_merge_empty_en_blocks_when_ru_has_indented_hrefs():
    ru = dedent("""
        items:
          - name: Auth
            href: auth.md
    """).strip()
    issues = validate_toc_merge(ru, "items:\n", translate_hrefs=set(), en_main_yaml="")
    assert any(issue.kind == "empty_toc" for issue in issues)


    toc = dedent("""
        items:
        - name: Overview
          href: index.md
        - include: { mode: link, path: toc_i.yaml }
    """).strip()
    items = parse_toc_items(toc)
    assert len(items) == 2
    assert items[0]["href"] == "index.md"
    assert items[1]["include_path"] == "toc_i.yaml"


def test_merge_en_toc_mirrors_absent_en_from_ru_with_inline_include():
    """Regression #46349: empty EN main mirrors full RU sidebar structure."""
    ru = dedent("""
        items:
        - name: Обзор
          href: index.md
        - include: { mode: link, path: toc_i.yaml }
    """).strip()
    merged = merge_en_toc_yaml(
        "",
        ru,
        translate_hrefs={"index.md"},
        translate_include_paths={"toc_i.yaml"},
        translate_name=lambda n: "Overview",
        ru_base_hrefs={"index.md"},
        ru_base_include_paths={"toc_i.yaml"},
        restrict_gap_fill_to_scope=False,
    )
    assert "index.md" in merged
    assert "toc_i.yaml" in merged
    assert "Overview" in merged
    issues = validate_toc_merge(
        ru,
        merged,
        translate_hrefs={"index.md"},
        translate_include_paths={"toc_i.yaml"},
        en_main_yaml="",
    )
    assert not any(issue.kind == "empty_toc" for issue in issues)


def test_merge_supplement_only_adds_translated_href_not_full_ru_gap():
    """Regression #44916: parent toc supplement must not pull unrelated RU renames."""
    en_main = dedent("""
        items:
        - name: actor_system_config
          href: actor_system_config.md
        - name: hive_config
          href: hive.md
        - name: kafka_proxy_config
          href: kafka.md
        - name: tls
          href: tls.md
    """).strip()
    ru_toc = dedent("""
        items:
        - name: actor_system_config
          href: actor_system_config.md
        - name: hive_config
          href: hive_config.md
        - name: kafka_proxy_config
          href: kafka_proxy_config.md
        - name: monitoring_config
          href: monitoring_config.md
        - name: system_tablet_backup_config
          href: system_tablet_backup_config.md
        - name: tls
          href: tls.md
    """).strip()
    base_hrefs = {
        it["href"] for it in parse_toc_items(ru_toc) if it.get("href")
    }
    merged = merge_en_toc_yaml(
        en_main,
        ru_toc,
        translate_hrefs={"system_tablet_backup_config.md"},
        translate_name=lambda n: n,
        ru_base_hrefs=base_hrefs,
        restrict_gap_fill_to_scope=True,
    )
    hrefs = [it["href"] for it in parse_toc_items(merged) if it.get("href")]
    assert "system_tablet_backup_config.md" in hrefs
    assert "hive_config.md" not in hrefs
    assert "kafka_proxy_config.md" not in hrefs
    assert "monitoring_config.md" not in hrefs
    assert hrefs.count("hive.md") == 1
    assert hrefs.count("kafka.md") == 1


def test_validate_toc_merge_accepts_legacy_href_alias_supplement():
    """Regression #44942: EN legacy hive.md/kafka.md must not block supplement merge."""
    en_main = dedent("""
        items:
        - name: actor_system_config
          href: actor_system_config.md
        - name: hive_config
          href: hive.md
        - name: kafka_proxy_config
          href: kafka.md
        - name: tls
          href: tls.md
    """).strip()
    ru_toc = dedent("""
        items:
        - name: actor_system_config
          href: actor_system_config.md
        - name: hive_config
          href: hive_config.md
        - name: kafka_proxy_config
          href: kafka_proxy_config.md
        - name: monitoring_config
          href: monitoring_config.md
        - name: system_tablet_backup_config
          href: system_tablet_backup_config.md
        - name: tls
          href: tls.md
    """).strip()
    base_hrefs = {
        it["href"] for it in parse_toc_items(ru_toc) if it.get("href")
    }
    scope = {"system_tablet_backup_config.md"}
    merged = merge_en_toc_yaml(
        en_main,
        ru_toc,
        translate_hrefs=scope,
        translate_name=lambda n: n,
        ru_base_hrefs=base_hrefs,
        restrict_gap_fill_to_scope=True,
    )
    issues = validate_toc_merge(
        ru_toc,
        merged,
        translate_hrefs=scope,
        en_main_yaml=en_main,
    )
    kinds = {i.kind for i in issues}
    # Scoped only_ru must not flag hive_config/kafka aliases (§6.124); EN-only
    # basename aliases remain soft toc_en_only_legacy.
    assert "toc_structure_parity" not in kinds
    assert "scope_not_applied" not in kinds
    assert kinds <= {"toc_en_only_legacy"}


def test_validate_toc_merge_flags_scoped_href_missing_from_en():
    en_main = dedent("""
        items:
        - name: tls
          href: tls.md
    """).strip()
    ru_toc = dedent("""
        items:
        - name: system_tablet_backup_config
          href: system_tablet_backup_config.md
        - name: tls
          href: tls.md
    """).strip()
    merged = en_main
    scope = {"system_tablet_backup_config.md"}
    issues = validate_toc_merge(
        ru_toc,
        merged,
        translate_hrefs=scope,
        en_main_yaml=en_main,
    )
    assert any(i.kind == "scope_not_applied" for i in issues)


def test_validate_toc_merge_legacy_alias_covers_scoped_ru_rename():
    """Scoped RU href rename satisfied when EN keeps legacy basename on main."""
    en_main = dedent("""
        items:
        - name: hive_config
          href: hive.md
    """).strip()
    ru_toc = dedent("""
        items:
        - name: hive_config
          href: hive_config.md
    """).strip()
    merged = en_main
    scope = {"hive_config.md"}
    issues = validate_toc_merge(
        ru_toc,
        merged,
        translate_hrefs=scope,
        en_main_yaml=en_main,
    )
    assert not any(i.kind == "scope_not_applied" for i in issues)


def test_merge_skips_ru_only_not_in_scope():
    """RU added new-page.md but it's not in translate_hrefs → not added to EN."""
    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs={"old.md"},
        translate_name=lambda n: "X",
        ru_base_hrefs={"old.md"},
    )
    hrefs = {it["href"] for it in parse_toc_items(merged)}
    assert "new-page.md" not in hrefs
    assert "old.md" in hrefs


ALTER_TABLE_RU_BASE = dedent("""
    items:
    - { name: Обзор,      href: index.md                                          }
    - { name: FAMILY,     href: family.md,          when: backend_name == "YDB"   }
""").strip()

ALTER_TABLE_RU_PR = dedent("""
    items:
    - { name: Обзор,      href: index.md                                          }
    - { name: FAMILY,     href: family.md,          when: backend_name == "YDB"   }
    - { name: COMPACT,    href: compact.md,         when: backend_name == "YDB"   }
""").strip()

ALTER_TABLE_EN_MAIN = dedent("""
    items:
     - { name: Overview,    href: index.md                                          }
     - { name: FAMILY,      href: family.md                                         }
""").strip()


def test_parse_toc_items_inline_ydb_format():
    items = parse_toc_items(ALTER_TABLE_RU_PR)
    assert len(items) == 3
    assert items[-1]["href"] == "compact.md"
    assert items[-1]["name"] == "COMPACT"


def test_merge_inline_toc_adds_compact_preserves_en_blocks():
    scope = toc_translate_scope(ALTER_TABLE_RU_BASE, ALTER_TABLE_RU_PR)
    assert scope.hrefs == {"compact.md"}

    merged = merge_en_toc_yaml(
        ALTER_TABLE_EN_MAIN,
        ALTER_TABLE_RU_PR,
        translate_hrefs=set(scope.hrefs),
        translate_name=lambda n: "COMPACT" if n == "COMPACT" else n,
    )
    items = parse_toc_items(merged)
    by_href = {it["href"]: it["name"] for it in items}

    assert len(items) == 3
    assert by_href["index.md"] == "Overview"
    assert by_href["family.md"] == "FAMILY"
    assert by_href["compact.md"] == "COMPACT"
    for line in merged.splitlines():
        if line.strip().startswith("- {"):
            assert line.startswith(" - {"), line


def test_merge_inline_toc_matches_alter_table_en_main_style():
    """Regression: PR #42726 — mixed ``- {`` / `` - {`` breaks Diplodoc YAML parse."""
    en_main = dedent("""
        items:
         - { name: Overview,    href: index.md                                          }
         - { name: INDEX,       href: indexes.md, when: feature_secondary_index }
         - { name: FAMILY,      href: family.md                                         }
    """).strip()
    ru_pr = dedent("""
        items:
        - { name: Обзор,      href: index.md                                          }
        - { name: INDEX,      href: indexes.md, when: feature_secondary_index }
        - { name: FAMILY,     href: family.md,          when: backend_name == "YDB"   }
        - { name: COMPACT,    href: compact.md,         when: backend_name == "YDB"   }
    """).strip()
    scope = {"compact.md"}
    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs=scope,
        translate_name=lambda n: "COMPACT" if n == "COMPACT" else n,
    )
    assert validate_toc_merge(ru_pr, merged, translate_hrefs=scope, en_main_yaml=en_main) == []
    for line in merged.splitlines():
        if line.strip().startswith("- {"):
            assert line.startswith(" - {"), line


def test_validate_toc_merge_flags_inconsistent_indent():
    bad = dedent("""
        items:
        - { name: A, href: a.md }
         - { name: B, href: b.md }
    """).strip()
    issues = validate_toc_merge("items:\n", bad, translate_hrefs=set(), en_main_yaml="items:\n")
    assert any(i.kind == "inconsistent_indent" for i in issues)


def test_validate_toc_merge_empty_inline_toc_is_blocking():
    scope = {"compact.md", "index.md"}
    issues = validate_toc_merge(
        ALTER_TABLE_RU_PR,
        "items:\n",
        translate_hrefs=scope,
        en_main_yaml=ALTER_TABLE_EN_MAIN,
    )
    kinds = {i.kind for i in issues}
    assert "empty_toc" in kinds
    assert "scope_not_applied" in kinds


def test_validate_toc_merge_clean():
    scope = toc_translate_scope(RU_BASE, RU_PR)
    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs=set(scope.hrefs),
        translate_name=lambda n: "T",
    )
    issues = validate_toc_merge(
        RU_PR, merged, translate_hrefs=set(scope.hrefs), en_main_yaml=EN_MAIN
    )
    kinds = {i.kind for i in issues}
    assert "toc_structure_parity" not in kinds
    assert "scope_not_applied" not in kinds
    assert kinds <= {"toc_en_only_legacy"}


YDB_SDK_EN_MAIN = dedent("""
    items:
    - name: Overview
      href: index.md
    - name: Authentication
      items:
      - name: Overview
        href: auth.md
      - name: Using a token
        href: auth-access-token.md
    - name: Troubleshooting
      items:
      - name: Overview
        href: debug.md
      - name: Enable logging
        href: debug-logs.md
      - name: Tracing with OpenTelemetry
        href: debug-otel.md
""").strip()

YDB_SDK_RU_BASE = dedent("""
    items:
    - name: Обзор
      href: index.md
    - name: Аутентификация
      items:
      - name: Обзор
        href: auth.md
      - name: С помощью токена
        href: auth-access-token.md
    - name: Диагностика проблем
      items:
      - name: Обзор
        href: debug.md
      - name: Включить логирование
        href: debug-logs.md
      - name: Трассировка с OpenTelemetry
        href: debug-otel.md
""").strip()

YDB_SDK_RU_PR = dedent("""
    items:
    - name: Обзор
      href: index.md
    - name: Аутентификация
      items:
      - name: Обзор
        href: auth.md
      - name: С помощью токена
        href: auth-access-token.md
    - name: Диагностика проблем
      items:
      - name: Обзор
        href: debug.md
      - name: Включить логирование
        href: debug-logs.md
      - name: Экспорт логов в OpenTelemetry
        href: debug-logs-otel.md
      - name: Трассировка с OpenTelemetry
        href: debug-otel.md
""").strip()


def test_parse_toc_items_nested_ydb_sdk_format():
    items = parse_toc_items(YDB_SDK_RU_PR)
    hrefs = [it["href"] for it in items]
    assert hrefs == [
        "index.md",
        "auth.md",
        "auth-access-token.md",
        "debug.md",
        "debug-logs.md",
        "debug-logs-otel.md",
        "debug-otel.md",
    ]


def test_toc_translate_scope_nested_detects_new_href():
    scope = toc_translate_scope(YDB_SDK_RU_BASE, YDB_SDK_RU_PR)
    assert scope.hrefs == {"debug-logs-otel.md"}


def test_merge_nested_toc_adds_otel_logs_preserves_structure():
    scope = toc_translate_scope(YDB_SDK_RU_BASE, YDB_SDK_RU_PR)

    def fake_translate(name: str) -> str:
        return {
            "Экспорт логов в OpenTelemetry": "Export logs to OpenTelemetry",
        }[name]

    merged = merge_en_toc_yaml(
        YDB_SDK_EN_MAIN,
        YDB_SDK_RU_PR,
        translate_hrefs=set(scope.hrefs),
        translate_name=fake_translate,
    )
    items = parse_toc_items(merged)
    by_href = {it["href"]: it["name"] for it in items}

    assert by_href["index.md"] == "Overview"
    assert by_href["auth.md"] == "Overview"
    assert by_href["auth-access-token.md"] == "Using a token"
    assert by_href["debug-logs-otel.md"] == "Export logs to OpenTelemetry"
    assert by_href["debug-logs.md"] == "Enable logging"

    hrefs = [it["href"] for it in items]
    assert hrefs.index("debug-logs.md") < hrefs.index("debug-logs-otel.md")
    assert hrefs.index("debug-logs-otel.md") < hrefs.index("debug-otel.md")

    assert "Authentication" in merged
    assert "Troubleshooting" in merged
    assert merged.count("- name: Overview") >= 2

    issues = validate_toc_merge(
        YDB_SDK_RU_PR,
        merged,
        translate_hrefs=set(scope.hrefs),
        en_main_yaml=YDB_SDK_EN_MAIN,
    )
    assert not issues


OBSERVABILITY_RU_PR = dedent("""
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
    - name: Трассировка
      include:
        mode: link
        path: tracing/toc_p.yaml
""").strip()


def test_parse_toc_items_include_links():
    """Regression #44103: ``include.path`` sidebar entries are parsed."""
    items = parse_toc_items(OBSERVABILITY_RU_PR)
    assert len(items) == 4
    assert items[0]["href"] == "index.md"
    assert items[1]["include_path"] == "logging/toc_p.yaml"
    assert items[1]["name"] == "Логирование"


def test_toc_translate_scope_detects_new_include_paths():
    scope = toc_translate_scope("", OBSERVABILITY_RU_PR)
    assert scope.hrefs == {"index.md"}
    assert scope.include_paths == {
        "logging/toc_p.yaml",
        "metrics/toc_p.yaml",
        "tracing/toc_p.yaml",
    }


def test_toc_translate_scope_handles_include_only_items_without_name():
    """Regression #46378/#46380: include-only lines have no name and must not crash."""
    ru = dedent(
        """
        items:
        - include: { mode: link, path: toc_i.yaml }
        """
    ).strip()
    scope = toc_translate_scope("", ru)
    assert scope.hrefs == frozenset()
    assert scope.include_paths == {"toc_i.yaml"}


def test_merge_toc_include_links_for_new_observability_section():
    """Regression #44103: parent toc_p.yaml must mirror RU include links."""
    scope = toc_translate_scope("", OBSERVABILITY_RU_PR)

    def fake_translate(name: str) -> str:
        return {
            "Обзор": "Overview",
            "Логирование": "Logging",
            "Метрики": "Metrics",
            "Трассировка": "Tracing",
        }[name]

    merged = merge_en_toc_yaml(
        "",
        OBSERVABILITY_RU_PR,
        translate_hrefs=set(scope.hrefs),
        translate_include_paths=set(scope.include_paths),
        translate_name=fake_translate,
    )
    assert "include:" in merged
    assert "logging/toc_p.yaml" in merged
    assert "metrics/toc_p.yaml" in merged
    assert "tracing/toc_p.yaml" in merged
    assert "- name: Logging" in merged
    assert "- name: Metrics" in merged
    assert "- name: Tracing" in merged

    issues = validate_toc_merge(
        OBSERVABILITY_RU_PR,
        merged,
        translate_hrefs=set(scope.hrefs),
        translate_include_paths=set(scope.include_paths),
        en_main_yaml="",
    )
    assert not issues


YDB_SDK_REF_EN_MAIN = dedent("""
    items:
      - name: Overview
        href: index.md
      - name: Installation
        href: install.md
      - name: Working with topics
        href: topic.md
      - name: Handling errors in the API
        href: error_handling.md
      - name: gRPC API
        items:
        - name: Overview
          href: overview-grpc-api.md
        - name: gRPC headers
          href: grpc-headers.md
      - name: Comparison of SDK features
        href: feature-parity.md
""").strip()

YDB_SDK_REF_RU_BASE = dedent("""
    items:
      - name: Обзор
        href: index.md
      - name: Установка SDK
        href: install.md
      - name: Работа с топиками
        href: topic.md
      - name: Обработка ошибок в API
        href: error_handling.md
      - name: gRPC API
        items:
        - name: Обзор
          href: overview-grpc-api.md
        - name: Заголовки gRPC
          href: grpc-headers.md
      - name: Сравнение возможностей SDK
        href: feature-parity.md
""").strip()

YDB_SDK_REF_RU_PR = dedent("""
    items:
      - name: Обзор
        href: index.md
      - name: Установка SDK
        href: install.md
      - name: Работа с топиками
        href: topic.md
      - name: Обработка ошибок в API
        href: error_handling.md
      - name: gRPC API
        items:
        - name: Обзор
          href: overview-grpc-api.md
        - name: Заголовки gRPC
          href: grpc-headers.md
      - name: Наблюдаемость
        include:
          mode: link
          path: observability/toc_p.yaml
      - name: Сравнение возможностей SDK
        href: feature-parity.md
""").strip()


def test_parse_indented_nested_ydb_sdk_reference_toc():
    """Regression #44117: 2-space top-level items under nested gRPC section."""
    items = parse_toc_items(YDB_SDK_REF_EN_MAIN)
    assert len(items) >= 7
    assert {it["href"] for it in items if it.get("href")} >= {
        "index.md",
        "topic.md",
        "error_handling.md",
        "overview-grpc-api.md",
    }


def test_merge_indented_nested_toc_adds_observability_include():
    """Regression #44117: nested indented toc must not collapse to empty items."""
    scope = toc_translate_scope(YDB_SDK_REF_RU_BASE, YDB_SDK_REF_RU_PR)
    assert scope.include_paths == {"observability/toc_p.yaml"}

    merged = merge_en_toc_yaml(
        YDB_SDK_REF_EN_MAIN,
        YDB_SDK_REF_RU_PR,
        translate_hrefs=set(scope.hrefs),
        translate_include_paths=set(scope.include_paths),
        translate_name=lambda n: {"Наблюдаемость": "Observability"}.get(n, n),
    )
    merged_items = parse_toc_items(merged)
    merged_hrefs = {it["href"] for it in merged_items if it.get("href")}
    assert "topic.md" in merged_hrefs
    assert "error_handling.md" in merged_hrefs
    assert "overview-grpc-api.md" in merged_hrefs
    assert "observability/toc_p.yaml" in {
        it["include_path"] for it in merged_items if it.get("include_path")
    }
    assert "- name: Observability" in merged
    assert len(merged_items) >= len(parse_toc_items(YDB_SDK_REF_EN_MAIN))

    issues = validate_toc_merge(
        YDB_SDK_REF_RU_PR,
        merged,
        translate_hrefs=set(scope.hrefs),
        translate_include_paths=set(scope.include_paths),
        en_main_yaml=YDB_SDK_REF_EN_MAIN,
    )
    assert not any(issue.kind == "collapsed_toc" for issue in issues)
    assert not any(issue.kind == "empty_toc" for issue in issues)


def test_merge_en_toc_drops_en_legacy_href_removed_from_ru_pr():
    en_main = dedent("""
        items:
        - name: Old page
          href: sql-dialect-converter.md
        - name: Section
          href: sql-translation/index.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: Section
          href: sql-translation/index.md
          include:
            mode: link
            path: sql-translation/toc-sql-translation.yaml
    """).strip()
    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs=set(),
        translate_name=lambda n: n,
        ru_base_hrefs={"sql-dialect-converter.md", "sql-translation/index.md"},
    )
    assert "sql-dialect-converter.md" not in merged
    assert "sql-translation/index.md" in merged


def test_merge_en_toc_preserves_en_only_local_and_external_topics():
    """Regression #46845: EN-only href not in RU must survive scoped merge."""
    en_main = dedent("""
        items:
        - name: Local and external topics
          href: local-and-external-topics.md
        - name: Common patterns
          href: patterns.md
        - name: Checkpoints
          href: checkpoints.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: Типичные шаблоны
          href: patterns.md
        - name: Чекпоинты
          href: checkpoints.md
    """).strip()
    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs={"patterns.md"},
        translate_name=lambda n: {
            "Типичные шаблоны": "Typical patterns",
            "Чекпоинты": "Checkpoints",
        }.get(n, n),
        ru_base_hrefs={"patterns.md", "checkpoints.md"},
        restrict_gap_fill_to_scope=True,
    )
    hrefs = [it.get("href") for it in parse_toc_items(merged)]
    assert "local-and-external-topics.md" in hrefs
    assert "patterns.md" in hrefs
    assert "checkpoints.md" in hrefs


def test_merge_en_toc_keep_en_hrefs_overrides_ru_base_drop():
    """§6.112: keep EN href when target page still exists on main."""
    en_main = dedent("""
        items:
        - name: Local and external topics
          href: local-and-external-topics.md
        - name: Patterns
          href: patterns.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: Patterns
          href: patterns.md
    """).strip()
    # Simulate: href was on RU base (would be dropped) but EN page still exists.
    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs=set(),
        translate_name=lambda n: n,
        ru_base_hrefs={"local-and-external-topics.md", "patterns.md"},
        keep_en_hrefs={"local-and-external-topics.md"},
    )
    hrefs = [it.get("href") for it in parse_toc_items(merged)]
    assert "local-and-external-topics.md" in hrefs
    assert "patterns.md" in hrefs


def test_parse_toc_items_keeps_include_path_alongside_href():
    """Regression #47100: Spring-style section has both href and include.path."""
    toc = dedent("""
        items:
        - name: Spring
          href: spring/index.md
          include:
            mode: link
            path: spring/toc-spring.yaml
    """).strip()
    items = parse_toc_items(toc)
    assert len(items) == 1
    assert items[0]["href"] == "spring/index.md"
    assert items[0]["include_path"] == "spring/toc-spring.yaml"


def test_validate_toc_merge_accepts_href_plus_include_section():
    """False scope_not_applied when EN already has section include (#47100)."""
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
    ru = dedent("""
        items:
        - name: Spring
          href: spring/index.md
          include:
            mode: link
            path: spring/toc-spring.yaml
        - name: Vector search
          href: vectorsearch/index.md
    """).strip()
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
    assert not any(i.kind == "scope_not_applied" for i in issues)


def test_merge_direct_toc_edit_does_not_gap_fill_ru_base_includes():
    """Regression #46258: Spring-only toc edit must not pull sql-translation include."""
    en_main = dedent("""
        items:
        - name: Vector search
          href: vector-search.md
        - name: SQL dialect converter
          href: sql-dialect-converter.md
    """).strip()
    ru_base = dedent("""
        items:
        - name: Vector search
          href: vector-search.md
        - name: SQL translation
          href: sql-translation/index.md
          include:
            mode: link
            path: sql-translation/toc-sql-translation.yaml
    """).strip()
    ru_pr = dedent("""
        items:
        - name: Vector search
          href: vector-search.md
        - name: SQL translation
          href: sql-translation/index.md
          include:
            mode: link
            path: sql-translation/toc-sql-translation.yaml
        - name: Spring
          href: spring/index.md
          include:
            mode: link
            path: spring/toc-spring.yaml
    """).strip()
    scope = toc_translate_scope(ru_base, ru_pr)
    assert scope.hrefs == {"spring/index.md"}
    assert scope.include_paths == {"spring/toc-spring.yaml"}

    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs=set(scope.hrefs),
        translate_include_paths=set(scope.include_paths),
        translate_name=lambda n: n,
        ru_base_hrefs={
            it["href"] for it in parse_toc_items(ru_base) if it.get("href")
        },
        ru_base_include_paths={
            it["include_path"]
            for it in parse_toc_items(ru_base)
            if it.get("include_path")
        },
        restrict_gap_fill_to_scope=True,
    )
    assert "spring/toc-spring.yaml" in merged
    assert "sql-translation/toc-sql-translation.yaml" not in merged
    assert "sql-dialect-converter.md" in merged
