from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def _git(cwd: str, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", cwd, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return p.stdout.strip()


def local_changed_paths(repo: str, merge_base_with: str) -> list[str]:
    mb = _git(repo, "merge-base", merge_base_with, "HEAD")
    out = _git(repo, "diff", "--name-only", mb, "HEAD")
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def read_text(repo: str, rel_path: str) -> str | None:
    path = Path(repo) / rel_path.replace("/", os.sep)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def write_text(repo: str, rel_path: str, content: str) -> None:
    path = Path(repo) / rel_path.replace("/", os.sep)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def git_commit_all(repo: str, message: str, author_name: str, author_email: str) -> bool:
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", author_name],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", author_email],
        check=True,
    )
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    st = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True)
    if not st.stdout.strip():
        return False
    subprocess.run(["git", "-C", repo, "commit", "-m", message], check=True)
    return True


def remote_push_url(https_clone_url: str, token: str) -> str:
    """
    Insert x-access-token into https://github.com/owner/repo.git
    """
    u = urlparse(https_clone_url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError(f"Unexpected remote URL: {https_clone_url}")
    host = u.hostname
    path = u.path or ""
    return f"https://x-access-token:{token}@{host}{path}"


def push_branch(repo: str, remote_name: str, branch: str, token: str, base_https_url: str) -> None:
    url = remote_push_url(base_https_url, token)
    subprocess.run(
        ["git", "-C", repo, "remote", "remove", remote_name],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo, "remote", "add", remote_name, url],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo, "push", remote_name, f"HEAD:refs/heads/{branch}"],
        check=True,
    )
