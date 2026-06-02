"""Tests for CI release label."""

from __future__ import annotations

import pytest

from ydbdoc_review.version import action_release_label


def test_action_release_label_ci(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTION_REF", "v0.1.0")
    monkeypatch.setenv("YDBDOC_GIT_SHA", "a1e8e92f3b82678b2c625232b1b4d1e74b5f4136")
    assert action_release_label() == "ydbdoc-review v0.1.0 @ a1e8e92"


def test_action_release_label_sha_only(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTION_REF", raising=False)
    monkeypatch.setenv("YDBDOC_GIT_SHA", "abcdef0123456789")
    assert action_release_label() == "ydbdoc-review @ abcdef0"
