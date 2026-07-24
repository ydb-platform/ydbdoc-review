"""Unit tests for ACL / quota gates."""

from ydbdoc_review.ops.gates import (
    acl_deny_comment,
    check_acl,
    check_daily_quota,
    expired_context_comment,
    parse_allowed_actors,
    quota_deny_comment,
    retention_notice,
)


def test_parse_allowed_actors_empty():
    assert parse_allowed_actors(None) == frozenset()
    assert parse_allowed_actors("") == frozenset()
    assert parse_allowed_actors("  ") == frozenset()


def test_parse_allowed_actors_list():
    assert parse_allowed_actors("sintjuri, alice ,bob") == frozenset(
        {"sintjuri", "alice", "bob"}
    )


def test_acl_empty_allowlist_allows_all():
    assert check_acl("anyone", frozenset()).ok


def test_acl_allow_and_deny():
    allowed = frozenset({"sintjuri"})
    assert check_acl("sintjuri", allowed).ok
    assert check_acl("SintJuri", allowed).ok
    denied = check_acl("hacker", allowed)
    assert not denied.ok
    assert denied.status == "denied_acl"
    assert "hacker" in acl_deny_comment("hacker")


def test_quota_gate():
    assert check_daily_quota(spent_rub=100, budget_rub=5000).ok
    denied = check_daily_quota(spent_rub=5000, budget_rub=5000)
    assert not denied.ok
    assert denied.status == "denied_quota"
    assert "5000" in quota_deny_comment(spent_rub=5000, budget_rub=5000)


def test_retention_and_expired_messages():
    assert "14" in retention_notice()
    text = expired_context_comment(41271)
    assert "ydbdoc-review/pr-41271" in text
    assert "doc_translate" in text
    assert "doc_verify" in text
