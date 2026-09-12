"""Progress / heartbeat helpers so long LLM waits are visible in CI logs."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def llm_call_heartbeat(
    *,
    role: object | None,
    model: str,
    interval_s: float = 30.0,
) -> Iterator[None]:
    """Log start/done and emit a heartbeat every ``interval_s`` while blocked."""
    role_s = str(role) if role is not None else "-"
    started = time.monotonic()
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval_s):
            logger.info(
                "LLM still waiting role=%s model=%s elapsed=%.0fs",
                role_s,
                model,
                time.monotonic() - started,
            )

    logger.info("LLM call start role=%s model=%s", role_s, model)
    thread = threading.Thread(
        target=_beat,
        name=f"llm-heartbeat-{model}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)
        logger.info(
            "LLM call done role=%s model=%s elapsed=%.1fs",
            role_s,
            model,
            time.monotonic() - started,
        )
