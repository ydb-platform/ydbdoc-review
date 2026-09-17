"""Infrastructure checks ONLY; passing these proves no product requirement."""
import socket
from decimal import Decimal

import pytest
from pytest_socket import SocketBlockedError

from .adapters import FakeModel, ModelReply


def test_network_is_blocked():
    with pytest.raises(SocketBlockedError):
        socket.socket()


def test_git_fixture_reads_exact_frozen_bytes(git_repo):
    repo, git = git_repo
    data = b"# Heading\r\n\r\n\x00asset\xff"
    (repo / "sample.bin").write_bytes(data)
    git("add", "sample.bin")
    git("commit", "-m", "snapshot")
    sha = git("rev-parse", "HEAD").decode().strip()
    (repo / "sample.bin").write_bytes(b"changed")
    git("add", "sample.bin")
    git("commit", "-m", "later")
    assert git("show", f"{sha}:sample.bin") == data
    assert git("rev-parse", "HEAD").decode().strip() != sha


def test_transport_fixture_records_paid_error_without_retry(trace):
    client = FakeModel(trace, [ModelReply(None, Decimal("0.12"), TimeoutError())])
    with pytest.raises(TimeoutError):
        client.call(role="critic", provider="primary", messages=[{"content": "source"}],
                    round=1, chunk=2)
    assert len(trace) == 1
    assert trace[0]["cost_rub"] == Decimal("0.12")
    assert trace[0]["error"] == "TimeoutError"


def test_store_fixture_distinguishes_empty_missing_and_outage(store):
    store.put("run_objects", "empty", b"")
    assert store.get("run_objects", "empty") == b""
    assert store.get("run_objects", "missing") is None
    store.error = ConnectionError("offline storage")
    with pytest.raises(ConnectionError):
        store.get("run_objects", "empty")
