"""MSK day helper."""

from ydbdoc_review.ops.msk import msk_today


def test_msk_today_format():
    day = msk_today()
    assert len(day) == 10
    assert day[4] == "-" and day[7] == "-"
