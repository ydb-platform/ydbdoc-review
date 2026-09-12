"""Cooperative shutdown for long-running CLI jobs (SIGINT/SIGTERM)."""

from __future__ import annotations

import signal
import threading
import time

_shutdown = threading.Event()
_handlers_installed = False


def request_shutdown() -> None:
    """Signal all workers to stop (idempotent)."""
    _shutdown.set()


def is_shutdown_requested() -> bool:
    return _shutdown.is_set()


def check_shutdown() -> None:
    """Raise KeyboardInterrupt when shutdown was requested."""
    if _shutdown.is_set():
        raise KeyboardInterrupt


def interruptible_sleep(seconds: float, *, chunk_s: float = 0.25) -> None:
    """Sleep in small chunks so worker threads can exit on shutdown."""
    if seconds <= 0:
        check_shutdown()
        return
    deadline = time.monotonic() + seconds
    while True:
        check_shutdown()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(chunk_s, remaining))


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
