from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def _git(cwd: str, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        err = (p.stderr or "").strip() or (p.stdout or "").strip() or "(no output)"
        raise RuntimeError(
            f"git -C {cwd} {' '.join(args)} failed (exit {p.returncode}): {err}"
        ) from None
    return p.stdout.strip()


def file_diff_range(repo: str, merge_base_with: str, rel_path: str) -> str:
    """
    Unified diff for rel_path between merge-base(merge_base_with, HEAD) and HEAD.
    Same range semantics as local_changed_paths (all PR commits vs base).
    """
    mb = _git(repo, "merge-base", merge_base_with, "HEAD")
    p = subprocess.run(
        ["git", "-C", repo, "diff", mb, "HEAD", "--", rel_path],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        err = (p.stderr or "").strip() or (p.stdout or "").strip() or "(no output)"
        raise RuntimeError(
            f"git -C {repo} diff {mb} HEAD -- {rel_path} failed (exit {p.returncode}): {err}"
        )
    return p.stdout


def merge_base(repo: str, ref1: str, ref2: str) -> str:
    """Merge base of two refs (same rules as `git merge-base`)."""
    return _git(repo, "merge-base", ref1, ref2)


def path_exists_at_tree(repo: str, rev: str, rel_path: str) -> bool:
    """True if `rev` has a blob at `rel_path` (git object path uses `/`)."""
    path = rel_path.replace(os.sep, "/")
    p = subprocess.run(
        ["git", "-C", repo, "cat-file", "-e", f"{rev}:{path}"],
        capture_output=True,
    )
    return p.returncode == 0


def checkout_new_branch(repo: str, branch: str) -> None:
    """Create (or reset) a local branch at current HEAD and check it out."""
    subprocess.run(["git", "-C", repo, "checkout", "-B", branch], check=True)


def ensure_remote(repo: str, name: str, url: str) -> None:
    subprocess.run(
        ["git", "-C", repo, "remote", "remove", name],
        capture_output=True,
    )
    subprocess.run(["git", "-C", repo, "remote", "add", name, url], check=True)


def fetch_remote_branch(repo: str, remote: str, branch: str) -> str:
    """Fetch one branch from remote; return local ref `refs/remotes/{remote}/{branch}`."""
    subprocess.run(
        ["git", "-C", repo, "fetch", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"],
        check=True,
    )
    return f"refs/remotes/{remote}/{branch}"


def checkout_branch_at_ref(repo: str, branch: str, start_ref: str) -> None:
    subprocess.run(["git", "-C", repo, "checkout", "-B", branch, start_ref], check=True)


def stash_paths(repo: str, paths: list[str], message: str = "ydbdoc-review") -> bool:
    if not paths:
        return False
    p = subprocess.run(
        ["git", "-C", repo, "stash", "push", "-m", message, "--"] + paths,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return False
    out = (p.stdout or "").strip().lower()
    err = (p.stderr or "").strip().lower()
    text = f"{out}\n{err}"
    return "no local changes to save" not in text


def stash_pop(repo: str) -> None:
    subprocess.run(["git", "-C", repo, "stash", "pop"], check=True)


def prepare_translation_branch_on_base(
    repo: str,
    *,
    translation_branch: str,
    base_remote_url: str,
    base_remote_name: str,
    base_branch: str,
    paths: list[str],
) -> None:
    """
    Move uncommitted translation files onto a new branch starting at upstream base
    (e.g. origin/main), for publishing a PR into the main repo — not the fork head.
    """
    stashed = stash_paths(repo, paths)
    ensure_remote(repo, base_remote_name, base_remote_url)
    base_ref = fetch_remote_branch(repo, base_remote_name, base_branch)
    checkout_branch_at_ref(repo, translation_branch, base_ref)
    if stashed:
        stash_pop(repo)


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
    # POSIX text files end with newline; LLM output often omits it → git shows the last
    # line as changed though visible text is identical ("\\ No newline at end of file").
    text = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


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
