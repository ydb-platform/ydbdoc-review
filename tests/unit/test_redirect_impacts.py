from pathlib import Path

from ydbdoc_review.validation.redirect_impacts import (
    added_redirects,
    mirror_redirects_to_en,
    retarget_redirect_inbound_links,
)


def test_added_redirects_and_inbound_retarget(tmp_path: Path):
    base = "ru:\n  - from: /old.md\n    to: /older.md\n"
    current = base + "  - from: /devops/manual/node.md\n    to: /devops/concepts/node.md\n"
    mappings = added_redirects(base, current)
    page = tmp_path / "ydb/docs/ru/core/security/authentication.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "See [nodes](../devops/manual/node.md#mode) and [other](other.md).\n",
        encoding="utf-8",
    )

    changed = retarget_redirect_inbound_links(str(tmp_path), mappings)

    assert changed == ["ydb/docs/ru/core/security/authentication.md"]
    assert page.read_text(encoding="utf-8") == (
        "See [nodes](../devops/concepts/node.md#mode) and [other](other.md).\n"
    )


def test_mirror_redirects_to_en_is_idempotent():
    text = "ru:\n  - from: /old.md\n    to: /new.md\n\nen:\n"
    mappings = {"/old.md": "/new.md"}

    mirrored = mirror_redirects_to_en(text, mappings)

    assert mirrored.endswith("en:\n  - from: /old.md\n    to: /new.md\n")
    assert mirror_redirects_to_en(mirrored, mappings) == mirrored
