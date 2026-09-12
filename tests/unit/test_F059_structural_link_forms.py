"""F-059: structural link forms and locale-safe include traversal."""

from ydbdoc_review.parsing.ast_types import (
    FencedCode,
    InlineHTML,
    InlineImage,
    InlineLink,
    YfmInclude,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.toc_targets import (
    _collect_reachable_include_dependencies,
    _relative_include_stays_in_locale,
)


def test_F059_forms():
    doc = parse_markdown(
        """\
[absolute](/ydb/docs/ru/core/absolute.md)
[relative](../relative.md)
[fragment](#local-section)
[reference][ref]
![diagram](https://example.com/diagram.svg)
<a href="/ydb/docs/ru/core/html.md">html</a>

[ref]: reference.md#section

{% include [details](_includes/details.md) %}
"""
    )

    paragraph = doc.children[0]
    links = [node for node in paragraph.children if isinstance(node, InlineLink)]
    assert [node.href for node in links] == [
        "/ydb/docs/ru/core/absolute.md",
        "../relative.md",
        "#local-section",
        "reference.md#section",
    ]
    image = next(node for node in paragraph.children if isinstance(node, InlineImage))
    assert image.src == "https://example.com/diagram.svg"
    html = next(node for node in paragraph.children if isinstance(node, InlineHTML))
    assert html.content == '<a href="/ydb/docs/ru/core/html.md">'
    assert isinstance(doc.children[1], YfmInclude)
    assert doc.children[1].path == "_includes/details.md"


def test_F059_code_and_traversal():
    doc = parse_markdown("```md\n[not-a-link](../en/core/secret.md)\n```\n")
    assert isinstance(doc.children[0], FencedCode)

    page = "ydb/docs/ru/core/section/page.md"
    assert _relative_include_stays_in_locale(
        page,
        "../_includes/details.md",
        docs_root="ydb/docs",
        locale="ru",
    )
    assert not _relative_include_stays_in_locale(
        page,
        "../../../en/core/secret.md",
        docs_root="ydb/docs",
        locale="ru",
    )

    files = {
        page: "{% include [details](_includes/details.md) %}\n",
        "ydb/docs/ru/core/section/_includes/details.md": "details\n",
        "ydb/docs/en/core/secret.md": "secret\n",
    }
    reachable = _collect_reachable_include_dependencies(
        files.get,
        toc_reachable={page},
        docs_root="ydb/docs",
        locale="ru",
    )
    assert reachable == frozenset(
        {
            page,
            "ydb/docs/ru/core/section/_includes/details.md",
        }
    )
