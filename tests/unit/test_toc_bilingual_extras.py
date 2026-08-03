"""§6.165: bilingual-skipped docs must not drive toc menu-label retranslation."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    planned_toc_extras_for_pair,
)
from ydbdoc_review.navigation.toc import parse_toc_items
from ydbdoc_review.pipeline.navigation_merge import _resolve_toc_merge_scope
from ydbdoc_review.pipeline.pairs import NavigationPair


CORE_TOC = dedent(
    """\
    items:
    - name: Начало работы
      href: quickstart.md
    - name: Администрирование кластеров
      href: devops/index.md
      include:
        mode: link
        path: devops/toc_p.yaml
    - name: Для контрибьюторов
      include:
        mode: link
        path: contributor/toc_p.yaml
    """
)

EN_TOC = dedent(
    """\
    items:
    - name: Quick start
      href: quickstart.md
    - name: Cluster Administration
      href: devops/index.md
      include:
        mode: link
        path: devops/toc_p.yaml
    - name: For Contributors
      include:
        mode: link
        path: contributor/toc_p.yaml
    """
)

DOC_TOC = dedent(
    """\
    items:
    - name: Style guide
      href: style-guide.md
    - name: Guide
      href: guide-to-public-material.md
    """
)


def _plan(*ru_docs: str) -> TranslationScopePlan:
    docs = frozenset(ru_docs)
    return TranslationScopePlan(
        doc_ru_paths=docs,
        doc_from_diff=docs,
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(
            {
                "ydb/docs/ru/core/toc_i.yaml",
                "ydb/docs/ru/core/contributor/documentation/toc_p.yaml",
            }
        ),
        nav_from_diff=frozenset({"ydb/docs/ru/core/toc_i.yaml"}),
        nav_from_main=frozenset(
            {"ydb/docs/ru/core/contributor/documentation/toc_p.yaml"}
        ),
    )


def test_planned_toc_extras_ignores_bilingual_skipped_docs():
    """#48411: full plan has quickstart+devops, but only guide is translated."""
    plan = _plan(
        "ydb/docs/ru/core/quickstart.md",
        "ydb/docs/ru/core/devops/index.md",
        "ydb/docs/ru/core/contributor/documentation/guide-to-public-material.md",
    )
    active = frozenset(
        {
            "ydb/docs/ru/core/contributor/documentation/guide-to-public-material.md",
        }
    )
    core_hrefs, _ = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/toc_i.yaml",
        CORE_TOC,
        active_doc_ru_paths=active,
    )
    assert core_hrefs == set()

    doc_hrefs, _ = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/contributor/documentation/toc_p.yaml",
        DOC_TOC,
        active_doc_ru_paths=active,
    )
    assert doc_hrefs == {"guide-to-public-material.md"}


def test_planned_toc_extras_without_filter_keeps_legacy_behavior():
    plan = _plan(
        "ydb/docs/ru/core/quickstart.md",
        "ydb/docs/ru/core/devops/index.md",
    )
    hrefs, _ = planned_toc_extras_for_pair(
        plan,
        "ydb/docs/ru/core/toc_i.yaml",
        CORE_TOC,
    )
    assert hrefs == {"quickstart.md", "devops/index.md"}


def test_bilingual_en_changed_drops_existing_en_hrefs_from_name_scope():
    """Author already renamed EN toc — do not re-LLM those labels."""
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/toc_i.yaml",
        en_path="ydb/docs/en/core/toc_i.yaml",
        ru_changed=True,
        en_changed=True,
    )
    ru_base = CORE_TOC.replace(
        "Администрирование кластеров", "Для администраторов БД"
    )
    scope, restrict = _resolve_toc_merge_scope(
        pair,
        ru_base=ru_base,
        ru_pr=CORE_TOC,
        en_main=EN_TOC,
        pair_extra_hrefs=set(),
    )
    assert restrict is True
    assert "devops/index.md" not in scope.hrefs
    assert "quickstart.md" not in scope.hrefs
    # Existing EN include kept out of name-translate scope.
    assert "devops/toc_p.yaml" not in scope.include_paths


def test_bilingual_keeps_new_page_extra_in_scope():
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/contributor/documentation/toc_p.yaml",
        en_path="ydb/docs/en/core/contributor/documentation/toc_p.yaml",
        ru_changed=True,
        en_changed=False,
    )
    en_main = dedent(
        """\
        items:
        - name: Style Guide
          href: style-guide.md
        """
    )
    scope, _ = _resolve_toc_merge_scope(
        pair,
        ru_base=en_main,  # unused for extras path
        ru_pr=DOC_TOC,
        en_main=en_main,
        pair_extra_hrefs={"guide-to-public-material.md"},
    )
    assert "guide-to-public-material.md" in scope.hrefs
    # style-guide already on EN and not an extra → not forced into scope by extras
    assert parse_toc_items(en_main)[0]["href"] == "style-guide.md"
