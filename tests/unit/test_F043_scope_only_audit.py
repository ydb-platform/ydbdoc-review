"""F-043 contracts for keeping orphan and redirect audits scope-local."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.validation.redirect_impacts import (
    added_redirects,
    retarget_redirect_inbound_links,
)
from ydbdoc_review.validation.toc_targets import check_orphan_pages_for_locale


def test_F043_historic_orphan(tmp_path: Path) -> None:
    root_toc = "ydb/docs/en/core/toc_p.yaml"
    scoped = "ydb/docs/en/core/changed.md"
    historic = "ydb/docs/en/core/old-orphan.md"
    (tmp_path / root_toc).parent.mkdir(parents=True)
    (tmp_path / root_toc).write_text("items:\n- name: Changed\n  href: changed.md\n")

    scoped_orphans = check_orphan_pages_for_locale(
        {scoped},
        repo_path=str(tmp_path),
        locale="en",
        pending_toc_texts={root_toc: "items:\n- name: Changed\n  href: changed.md\n"},
        pending_md_texts={scoped: "# Changed\n"},
    )
    with_historic_orphan = check_orphan_pages_for_locale(
        {scoped, historic},
        repo_path=str(tmp_path),
        locale="en",
        pending_toc_texts={root_toc: "items:\n- name: Changed\n  href: changed.md\n"},
        pending_md_texts={scoped: "# Changed\n", historic: "# Old\n"},
    )

    assert scoped_orphans == {}
    assert set(with_historic_orphan) == {historic}


def test_F043_incoming_usage(tmp_path: Path) -> None:
    base = "ru:\n  - from: /old.md\n    to: /new.md\n"
    current = (
        "ru:\n"
        "  - from: /old.md\n"
        "    to: /new.md\n"
        "  - from: /changed.md\n"
        "    to: /new.md\n"
    )

    mappings = added_redirects(base, current)
    assert mappings == {
        "/changed.md": "/new.md",
    }

    scoped = tmp_path / "ydb/docs/ru/core/scoped.md"
    foreign = tmp_path / "ydb/docs/ru/core/foreign.md"
    scoped.parent.mkdir(parents=True)
    scoped.write_text("[changed](changed.md)\n")
    foreign.write_text("[changed](changed.md)\n")

    changed = retarget_redirect_inbound_links(
        str(tmp_path), mappings, allowed_paths={"ydb/docs/ru/core/scoped.md"}
    )
    assert changed == ["ydb/docs/ru/core/scoped.md"]
    assert "new.md" in scoped.read_text()
    assert foreign.read_text() == "[changed](changed.md)\n"
