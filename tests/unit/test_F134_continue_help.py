from ydbdoc_review.ops.gates import (
    expired_context_comment,
    retention_notice,
    store_unavailable_comment,
)


def test_F134_available() -> None:
    message = retention_notice(continue_used=1, continue_limit=3)
    assert message.index("/ydbdoc continue <что исправить>") < message.index("doc_continue")
    assert "14 дней" in message
    assert "1/3" in message and "осталось: **2**" in message


def test_F134_unavailable() -> None:
    expired = expired_context_comment(134)
    unavailable = store_unavailable_comment(134, detail="YDB unavailable")
    limited = retention_notice(continue_used=3, continue_limit=3)

    assert "Continue недоступен" in expired
    assert "doc_translate" in expired and "doc_verify" in expired
    assert "continue недоступен" in unavailable.lower()
    assert "doc_translate" in unavailable and "doc_verify" in unavailable
    assert "осталось: **0**" in limited
    assert "/ydbdoc continue" in limited
