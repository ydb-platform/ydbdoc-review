"""F-073: pair redirects require explicit, scoped evidence."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.redirects import (
    iter_redirect_mappings,
    merge_en_redirects_yaml,
    redirect_translate_scope,
    validate_redirect_merge,
)


def test_F073_valid_evidence() -> None:
    """Changed, newly explicit and tech-writer-scoped entries become symmetric."""
    base = dedent(
        """
        - from: /renamed
          to: /old-target
        """
    ).strip()
    source = dedent(
        """
        - from: /renamed
          to: /new-target
        - from: /explicit
          to: /explicit-target
        - from: /tech-writer
          to: /approved-target
        """
    ).strip()
    automatic_scope = redirect_translate_scope(base, source)
    scope = automatic_scope | {"/tech-writer"}
    merged = merge_en_redirects_yaml(
        "",
        source,
        translate_from_paths=scope,
    )
    mappings = iter_redirect_mappings(merged)

    assert mappings == {
        "/renamed": "/new-target",
        "/explicit": "/explicit-target",
        "/tech-writer": "/approved-target",
    }
    assert not validate_redirect_merge(
        source,
        merged,
        translate_from_paths=scope,
        en_main_yaml="",
    )


def test_F073_insufficient_evidence() -> None:
    """Similarity or an added page alone cannot authorize the A→B migration."""
    source = dedent(
        """
        - from: /old-path
          to: /new-path
        - from: /added-page
          to: /added-target
        """
    ).strip()
    merged = merge_en_redirects_yaml(
        "",
        source,
        translate_from_paths=set(),
    )

    assert iter_redirect_mappings(merged) == {}
    issues = validate_redirect_merge(
        source,
        merged,
        translate_from_paths={"/old-path", "/added-page"},
        en_main_yaml="",
    )
    assert {issue.kind for issue in issues} == {"missing_from", "scope_not_applied"}
    scoped_issues = [issue for issue in issues if issue.kind == "scope_not_applied"]
    assert {"/old-path", "/added-page"} == {
        issue.detail.split("from ", 1)[1].split(" ", 1)[0].strip("'")
        for issue in scoped_issues
    }
