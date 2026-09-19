"""Align model attempt timestamps with an explicitly controlled offline store day."""
from datetime import datetime


def model_clock(monkeypatch, now):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz)
    monkeypatch.setattr('ydbdoc_review.model.datetime', Clock)
