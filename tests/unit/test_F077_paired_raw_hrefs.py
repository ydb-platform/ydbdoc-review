"""F-077: paired links migrate together without broad link cleanup."""

from __future__ import annotations

from ydbdoc_review.validation.href_parity import (
    check_href_parity,
    retarget_source_owned_redirect_hrefs,
)

PAGE = "ydb/docs/en/core/guide/index.md"
OLD = "../legacy/page.md#topic"
NEW = "../current/page.md#topic"
REDIRECTS = (
    "common:\n"
    "  - from: /legacy/page.md\n"
    "    to: /current/page.md\n"
)


def _reader(path: str) -> str | None:
    return {
        "ydb/docs/redirects.yaml": REDIRECTS,
        "ydb/docs/en/core/current/page.md": "# Current {#topic}\n",
    }.get(path)


def test_F077_raw_pair() -> None:
    ru = f"[topic]({OLD})\n"
    assert check_href_parity(ru, ru, en_baseline_text=ru) == []

    # A redirect to the same final page does not make raw A/B a literal pair.
    en_b = f"[topic]({NEW})\n"
    issues = check_href_parity(ru, en_b, en_baseline_text=ru)
    assert issues
    assert "href_parity" in issues[0]


def test_F077_paired_migration() -> None:
    source = f"[topic]({OLD})\n[foreign](../other/page.md)\n"
    target = source
    assert retarget_source_owned_redirect_hrefs(
        target,
        source,
        en_page_path=PAGE,
        read_text=_reader,
    ) == f"[topic]({NEW})\n[foreign](../other/page.md)\n"

    # The pair remains acceptable after the source-scoped migration.
    assert check_href_parity(
        source,
        f"[topic]({NEW})\n[foreign](../other/page.md)\n",
        en_page_path=PAGE,
        docs_text_reader=_reader,
    ) == []
