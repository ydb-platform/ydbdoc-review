"""Infrastructure checks ONLY; passing these proves no product requirement."""
import socket

import pytest
from pytest_socket import SocketBlockedError


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
