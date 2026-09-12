"""Tests for redirect YAML scoped merge."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.redirects import (
    merge_en_redirects_yaml,
    parse_redirect_entries,
    redirect_translate_scope,
    validate_redirect_merge,
)

RU_BASE = dedent("""
    - from: /old-path
      to: /target-a
    - from: /stable
      to: /same-target
""").strip()

RU_PR = dedent("""
    - from: /old-path
      to: /target-b
    - from: /stable
      to: /same-target
    - from: /brand-new
      to: /new-target
""").strip()

EN_MAIN = dedent("""
    - from: /old-path
      to: /target-a
    - from: /stable
      to: /same-target
    - from: /en-only
      to: /en-target
""").strip()


def test_parse_redirect_entries():
    entries = parse_redirect_entries(RU_PR)
    assert len(entries) == 3
    assert entries[0]["from_path"] == "/old-path"


def test_redirect_translate_scope():
    scope = redirect_translate_scope(RU_BASE, RU_PR)
    assert scope == {"/old-path", "/brand-new"}
    assert "/stable" not in scope


def test_merge_keeps_unchanged_en_redirects():
    scope = redirect_translate_scope(RU_BASE, RU_PR)
    merged = merge_en_redirects_yaml(
        EN_MAIN,
        RU_PR,
        translate_from_paths=scope,
    )
    entries = parse_redirect_entries(merged)
    by_from = {e["from_path"]: e["to_path"] for e in entries}

    assert by_from["/stable"] == "/same-target"
    assert by_from["/old-path"] == "/target-b"
    assert by_from["/brand-new"] == "/new-target"
    assert by_from["/en-only"] == "/en-target"


def test_merge_skips_ru_only_not_in_scope():
    merged = merge_en_redirects_yaml(
        EN_MAIN,
        RU_PR,
        translate_from_paths={"/old-path"},
    )
    froms = {e["from_path"] for e in parse_redirect_entries(merged)}
    assert "/brand-new" not in froms


def test_validate_redirect_merge_clean():
    scope = redirect_translate_scope(RU_BASE, RU_PR)
    merged = merge_en_redirects_yaml(EN_MAIN, RU_PR, translate_from_paths=scope)
    issues = validate_redirect_merge(
        RU_PR, merged, translate_from_paths=scope, en_main_yaml=EN_MAIN
    )
    assert not issues
