"""Cooperative shutdown for long-running CLI jobs (SIGINT/SIGTERM)."""

from __future__ import annotations

import signal
import threading

_shutdown = threading.Event()
_handlers_installed = False


def request_shutdown() -> None:
    """Signal all workers to stop (idempotent)."""
    _shutdown.set()


def is_shutdown_requested() -> bool:
    return _shutdown.is_set()






def install_shutdown_handlers() -> None:
    """Register SIGINT/SIGTERM handlers once per process."""
    global _handlers_installed
    if _handlers_installed:
        return

    def _handle(signum: int, _frame: object | None) -> None:
        request_shutdown()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    _handlers_installed = True
