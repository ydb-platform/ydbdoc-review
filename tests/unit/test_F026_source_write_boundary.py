"""F-026: source-locale files stay outside the translation write boundary."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.validation.redirect_impacts import retarget_redirect_inbound_links


def test_F026_source_untouched(tmp_path: Path) -> None:
    ru_page = tmp_path / "ydb/docs/ru/core/guide/page.md"
    en_page = tmp_path / "ydb/docs/en/core/guide/page.md"
    ru_page.parent.mkdir(parents=True)
    en_page.parent.mkdir(parents=True)
    ru_before = "See [old](../old/page.md).\n"
    en_before = "See [old](../old/page.md).\n"
    ru_page.write_text(ru_before, encoding="utf-8")
    en_page.write_text(en_before, encoding="utf-8")

    changed = retarget_redirect_inbound_links(
        str(tmp_path),
        {"/old/page.md": "/new/page.md"},
        allowed_paths=frozenset({"ydb/docs/en/core/guide/page.md"}),
    )

    assert changed == ["ydb/docs/en/core/guide/page.md"]
    assert ru_page.read_text(encoding="utf-8") == ru_before
    assert en_page.read_text(encoding="utf-8") == "See [old](../new/page.md).\n"


def test_F026_narrow_exception() -> None:
    """The permitted address migration is an EN href write, not source prose repair."""
    from ydbdoc_review.validation.redirect_impacts import propose_redirect_inbound_link_writes

    files = {
        "ydb/docs/ru/core/guide/page.md": "See [old](../old/page.md).\n",
        "ydb/docs/en/core/guide/page.md": "See [old](../old/page.md).\n",
    }
    writes = propose_redirect_inbound_link_writes(
        frozenset({"ydb/docs/en/core/guide/page.md"}),
        {"/old/page.md": "/new/page.md"},
        read_text=files.get,
    )

    assert writes == {"ydb/docs/en/core/guide/page.md": "See [old](../new/page.md).\n"}
    assert "ydb/docs/ru/core/guide/page.md" not in writes
