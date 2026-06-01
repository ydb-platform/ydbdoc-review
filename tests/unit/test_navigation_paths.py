"""Tests for navigation path detection."""

from __future__ import annotations

from ydbdoc_review.navigation.paths import (
    is_navigation_yaml,
    is_redirect_yaml,
    is_toc_yaml,
    navigation_yaml_kind,
)


def test_is_toc_yaml():
    assert is_toc_yaml("ydb/docs/ru/toc.yaml")
    assert is_toc_yaml("ydb/docs/en/toc_i.yaml")
    assert not is_toc_yaml("ydb/docs/ru/page.md")


def test_is_redirect_yaml():
    assert is_redirect_yaml("ydb/docs/ru/redirects.yaml")
    assert is_redirect_yaml("ydb/docs/en/redirect.yaml")
    assert not is_redirect_yaml("ydb/docs/ru/toc.yaml")


def test_navigation_yaml_kind():
    assert navigation_yaml_kind("ydb/docs/ru/toc.yaml") == "toc"
    assert navigation_yaml_kind("ydb/docs/ru/redirects.yaml") == "redirect"
    assert navigation_yaml_kind("ydb/docs/ru/a.md") is None
    assert is_navigation_yaml("ydb/docs/ru/toc.yaml")
