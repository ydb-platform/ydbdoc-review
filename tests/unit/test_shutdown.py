"""Tests for cooperative shutdown helpers."""

from __future__ import annotations

import signal
from unittest.mock import patch

import pytest

from ydbdoc_review import shutdown


@pytest.fixture(autouse=True)
def _reset_shutdown():
    shutdown._shutdown.clear()
    shutdown._handlers_installed = False
    yield
    shutdown._shutdown.clear()
    shutdown._handlers_installed = False


def test_interruptible_sleep_raises_when_shutdown_requested():
    shutdown.request_shutdown()
    with pytest.raises(KeyboardInterrupt):
        shutdown.interruptible_sleep(10.0)


def test_install_shutdown_handlers_sigint():
    shutdown.install_shutdown_handlers()
    with pytest.raises(KeyboardInterrupt):
        signal.raise_signal(signal.SIGINT)
