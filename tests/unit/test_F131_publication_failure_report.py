import logging

import pytest

from ydbdoc_review.github.workflow import _publication_failure_error


def test_F131_partial_failure() -> None:
    error = _publication_failure_error(
        stage="push",
        branch="ydbdoc-review/pr-131",
        candidate_sha="deadbeef1234",
        pr_number=9001,
        reason="remote branch changed; existing artifact retained; cost=12.50 RUB",
    )
    message = str(error)

    assert "branch=ydbdoc-review/pr-131" in message
    assert "PR=#9001" in message
    assert "K=deadbeef1234" in message
    assert "existing artifact retained" in message
    assert "doc_translate" in message
    assert "no automatic retry" in message


def test_F131_comment_failure(caplog) -> None:
    error = _publication_failure_error(
        stage="comment",
        branch="ydbdoc-review/pr-131",
        candidate_sha="deadbeef1234",
        reason="GitHub comment unavailable",
    )
    with caplog.at_level(logging.ERROR):
        logging.getLogger("ydbdoc_review.github.workflow").error("%s", error)

    assert "GitHub comment unavailable" in caplog.text
    assert "doc_translate" in caplog.text
    assert "K=deadbeef1234" in caplog.text
    with pytest.raises(RuntimeError, match="no automatic retry"):
        raise error
