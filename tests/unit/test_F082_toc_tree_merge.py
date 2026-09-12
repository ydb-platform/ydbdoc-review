"""F-082: scoped TOC updates retain nested structure and neighbors."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.toc import merge_en_toc_yaml, parse_toc_items

EN_MAIN = dedent(
    """
    items:
      - name: Overview
        href: index.md
      - name: API
        items:
        - name: Errors
          href: errors.md
      - name: Comparison
        href: comparison.md
    """
).strip()
RU_PR = dedent(
    """
    items:
      - name: Сравнение
        href: comparison.md
      - name: API
        items:
        - name: Ошибки
          href: errors.md
      - name: Наблюдаемость
        include:
          mode: link
          path: observability/toc_p.yaml
      - name: Обзор
        href: index.md
    """
).strip()


def _translate(name: str) -> str:
    return {
        "Сравнение": "Comparison",
        "Ошибки": "Errors",
        "Наблюдаемость": "Observability",
        "Обзор": "Overview",
    }.get(name, name)


def test_F082_nested_merge() -> None:
    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs={"comparison.md", "index.md"},
        translate_include_paths={"observability/toc_p.yaml"},
        translate_name=_translate,
        ru_base_hrefs={"comparison.md", "index.md", "errors.md"},
        ru_base_include_paths=set(),
    )
    assert merged.index("comparison.md") < merged.index("errors.md")
    assert "- name: Observability" in merged
    assert "path: observability/toc_p.yaml" in merged
    assert "errors.md" in merged


def test_F082_no_duplicates() -> None:
    kwargs = {
        "translate_hrefs": {"comparison.md", "index.md"},
        "translate_include_paths": {"observability/toc_p.yaml"},
        "translate_name": _translate,
        "ru_base_hrefs": {"comparison.md", "index.md", "errors.md"},
        "ru_base_include_paths": set(),
    }
    first = merge_en_toc_yaml(EN_MAIN, RU_PR, **kwargs)
    second = merge_en_toc_yaml(first, RU_PR, **kwargs)
    assert second.count("comparison.md") == 1
    assert second.count("errors.md") == 1
    assert second.count("observability/toc_p.yaml") == 1
    assert {item.get("href") for item in parse_toc_items(second) if item.get("href")} >= {
        "comparison.md",
        "errors.md",
        "index.md",
    }
