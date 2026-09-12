"""F-076: redirects preserve proven fragments and retain ambiguous links."""

from __future__ import annotations

from ydbdoc_review.reporting.heuristic_messages import format_heuristic_reviewer_detail
from ydbdoc_review.validation.href_parity import redirected_md_href

PAGE = "ydb/docs/en/core/guide/index.md"
OLD_HREF = "../legacy/page.md#topic"
REDIRECTS = (
    "common:\n"
    "  - from: /legacy/page.md\n"
    "    to: /current/page.md\n"
)


def test_F076_mapped_sections() -> None:
    files = {
        "ydb/docs/redirects.yaml": REDIRECTS,
        "ydb/docs/en/core/current/page.md": "# Current\n## Topic {#topic}\n",
    }
    assert redirected_md_href(
        OLD_HREF,
        en_page_path=PAGE,
        read_text=files.get,
    ) == "../current/page.md#topic"


def test_F076_ambiguous_merge() -> None:
    files = {
        "ydb/docs/redirects.yaml": REDIRECTS,
        "ydb/docs/en/core/current/page.md": "# Current\n## First {#first}\n## Second {#second}\n",
    }
    assert redirected_md_href(
        "../legacy/page.md#merged-topic",
        en_page_path=PAGE,
        read_text=files.get,
    ) is None

    detail = format_heuristic_reviewer_detail(
        "outbound_fragment: `../legacy/page.md#merged-topic` has no proven target"
    )
    assert detail.suggestion is not None
    assert "/ydbdoc continue <адрес#fragment>" in detail.suggestion
    assert "старый" in detail.suggestion
