"""Role-based launch checks for §2 F-001."""

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.ops.lifecycle import begin_ops_job


def test_F001_author_without_allowlist_is_rejected():
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=1,
        env={
            "YDBDOC_ALLOWED_ACTORS": "svc-initiator,tech-lead",
            "GITHUB_ACTOR": "source-pr-author",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
    )

    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_acl"
    assert comment is not None
    assert "allowlist" in comment


def test_F001_authorized_initiator_or_external_system_can_start_job():
    for actor in ("svc-initiator", "github-actions[bot]"):
        ctx, gate, comment = begin_ops_job(
            mode="translate",
            repo="o/r",
            source_pr=1,
            env={
                "YDBDOC_ALLOWED_ACTORS": "svc-initiator,github-actions[bot],tech-lead",
                "GITHUB_ACTOR": actor,
                "YDBDOC_DAILY_BUDGET_RUB": "5000",
            },
        )

        assert ctx is not None
        assert gate.ok
        assert gate.status == "ok"
        assert comment is None
        assert ctx.actor == actor


def test_F001_no_automatic_merge_api_method_is_exposed():
    assert not hasattr(GitHubClient, "merge_pull_request")
