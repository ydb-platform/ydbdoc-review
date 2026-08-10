"""Progress / heartbeat helpers."""

from __future__ import annotations

import logging
import time

from ydbdoc_review.llm.progress import llm_call_heartbeat


def test_llm_call_heartbeat_emits_waiting(caplog):
    with caplog.at_level(logging.INFO, logger="ydbdoc_review.llm.progress"):
        with llm_call_heartbeat(role="translate", model="test-model", interval_s=0.05):
            time.sleep(0.12)
    messages = [r.getMessage() for r in caplog.records]
    assert any("LLM call start" in m and "test-model" in m for m in messages)
    assert any("LLM still waiting" in m for m in messages)
    assert any("LLM call done" in m for m in messages)
