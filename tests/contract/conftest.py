"""Isolated fixtures. No imports from legacy test modules or workflow runners."""
import os
import subprocess

import pytest


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch, socket_disabled):
    # Contract tests receive only explicit dummy configuration. Never pass real secrets
    # to a child process or accidentally instantiate a paid provider with host credentials.
    for name in tuple(os.environ):
        if any(part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "SA_KEY", "CREDENTIAL")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, timeout=15).stdout
    git("init")
    git("symbolic-ref", "HEAD", "refs/heads/main")
    git("config", "user.name", "Contract Test")
    git("config", "user.email", "contract@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("commit", "--allow-empty", "-m", "initial")
    return repo, git
