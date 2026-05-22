"""Tests for structural RU→EN sync (PR #39667 / #41015 regression)."""

from ydbdoc_review.ru_en_structure import (
    apply_structure_sync_from_ru,
    index_bullets_behind_ru,
    list_tab_item_labels,
    markdown_bullet_hrefs,
    reorder_bullet_links_like_ru,
    sync_index_bullets_from_ru,
    sync_list_tab_items_from_ru,
    tab_items_missing_vs_source,
)

RU_JAEGER_SNIPPET = """\
# Jaeger

{% list tabs %}

- C++

  Функциональность на данный момент не поддерживается.

- Go

  code

- Java

  {% include [feature-not-supported](../../_includes/feature-not-supported.md) %}

- Python

  {% include [feature-not-supported](../../_includes/feature-not-supported.md) %}

{% endlist %}
"""

EN_JAEGER_STALE = """\
# Jaeger

{% list tabs %}

- Go

  code

- Java

  {% include [feature-not-supported](../../_includes/feature-not-supported.md) %}

{% endlist %}
"""


def test_tab_items_missing_detected():
    assert tab_items_missing_vs_source(RU_JAEGER_SNIPPET, EN_JAEGER_STALE)


def test_sync_list_tab_items_adds_cpp_and_python():
    out = sync_list_tab_items_from_ru(RU_JAEGER_SNIPPET, EN_JAEGER_STALE)
    labels = list_tab_item_labels(out)
    assert "C++" in labels
    assert "Python" in labels
    assert "Go" in labels
    assert "This functionality is not currently supported." in out


def test_index_bullets_sync_and_order():
    ru = """\
# Index

- [Logging](debug-logs.md)
- [Metrics](debug-prometheus.md)
- [OpenTelemetry](debug-otel.md)
- [Jaeger](debug-jaeger.md)
- [Vector search](vector-search.md)
"""
    en = """\
# Index

- [Logging](debug-logs.md)
- [Metrics](debug-prometheus.md)
- [Jaeger](debug-jaeger.md)
- [OpenTelemetry](debug-otel.md)
"""
    assert index_bullets_behind_ru(ru, en)
    out = sync_index_bullets_from_ru(ru, en)
    hrefs = [h for _, h in markdown_bullet_hrefs(out)]
    assert "vector-search.md" in hrefs
    assert hrefs.index("debug-otel.md") < hrefs.index("debug-jaeger.md")


def test_reorder_bullet_links_like_ru():
    ru = """\
- [A](a.md)
- [B](b.md)
"""
    en = """\
- [B](b.md)
- [A](a.md)
"""
    out = reorder_bullet_links_like_ru(ru, en)
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("- [")]
    assert lines[0].endswith("(a.md)")
    assert lines[1].endswith("(b.md)")


def test_nested_list_tabs_span_parses_trailing_sdk_items():
    """Regression: nested Go tabs must not close the outer span at inner {% endlist %}."""
    ru = """\
{% list tabs %}

- C++

  not supported

- Go

  {% list tabs %}

  - Native SDK

      code

  {% endlist %}

- Rust

  {% include [feature-not-supported](../../_includes/feature-not-supported.md) %}

{% endlist %}
"""
    en = """\
{% list tabs %}

- Go

  {% list tabs %}

  - Native SDK

      code

  {% endlist %}

{% endlist %}
"""
    out = sync_list_tab_items_from_ru(ru, en)
    assert "Rust" in list_tab_item_labels(out)
    assert "C++" in list_tab_item_labels(out)


def test_apply_structure_sync_combined():
    out = apply_structure_sync_from_ru(RU_JAEGER_SNIPPET, EN_JAEGER_STALE)
    assert not tab_items_missing_vs_source(RU_JAEGER_SNIPPET, out)
