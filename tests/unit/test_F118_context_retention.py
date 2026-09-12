from datetime import UTC, datetime, timedelta

from ydbdoc_review.ops.gates import (
    TRANSCRIPT_RETENTION,
    retention_expires_at,
    retention_notice,
)


def test_F118_ttl_boundary() -> None:
    saved_at = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    expires_at = retention_expires_at(saved_at)

    assert saved_at + TRANSCRIPT_RETENTION - timedelta(seconds=1) < expires_at
    assert saved_at + TRANSCRIPT_RETENTION == expires_at


def test_F118_report() -> None:
    saved_at = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    report = retention_notice(
        saved_at=saved_at,
        continue_used=2,
        continue_limit=3,
        launch_mode="doc_continue",
    )

    assert "2026-09-26 10:00 UTC" in report
    assert "2/3" in report
    assert "осталось: **1**" in report
    assert "doc_continue" in report
