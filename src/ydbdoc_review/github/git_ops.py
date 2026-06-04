"""Local git operations for the translation workflow."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from ydbdoc_review.pipeline.pairs import ChangeKind


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or "(no output)"
        raise RuntimeError(
            f"git -C {repo} {' '.join(args)} failed (exit {proc.returncode}): {err}"
        )
    return proc.stdout.strip()


def merge_base(repo: str, ref1: str, ref2: str) -> str:
    return _git(repo, "merge-base", ref1, ref2)


def git_head_sha(repo: str) -> str | None:
    """Current HEAD commit in ``repo``, or None if not a git checkout."""
    try:
        return _git(repo, "rev-parse", "HEAD")
    except RuntimeError:
        return None


def list_local_changes(
    repo: str, merge_base_with: str
) -> list[tuple[str, ChangeKind]]:
    """Paths changed between merge-base and HEAD with change kind."""
    mb = merge_base(repo, merge_base_with, "HEAD")
    proc = subprocess.run(
        ["git", "-C", repo, "diff", "--name-status", mb, "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    out: list[tuple[str, ChangeKind]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, path = parts[0].strip(), parts[1].strip()
        if status.startswith("R") and "\t" in line:
            # rename: R100\told\tnew — take new path
            rename_parts = line.split("\t")
            if len(rename_parts) >= 3:
                path = rename_parts[2].strip()
            status = "M"
        kind: ChangeKind
        if status == "A":
            kind = "added"
        elif status == "D":
            kind = "deleted"
        else:
            kind = "modified"
        out.append((path.replace("\\", "/"), kind))
    return out


def file_diff_range(repo: str, merge_base_with: str, rel_path: str) -> str:
    mb = merge_base(repo, merge_base_with, "HEAD")
    proc = subprocess.run(
        ["git", "-C", repo, "diff", mb, "HEAD", "--", rel_path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git diff failed for {rel_path}: {err}")
    return proc.stdout or ""


def read_text(repo: str, rel_path: str) -> str | None:
    path = Path(repo) / rel_path.replace("/", os.sep)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def read_text_at_ref(repo: str, ref: str, rel_path: str) -> str | None:
    path = rel_path.replace(os.sep, "/")
    proc = subprocess.run(
        ["git", "-C", repo, "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def write_text(repo: str, rel_path: str, content: str) -> None:
    path = Path(repo) / rel_path.replace("/", os.sep)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _remote_tracking_ref(remote: str, branch: str) -> str:
    safe = branch.replace("/", "--")
    return f"refs/remotes/{remote}/{safe}"


def ensure_remote(repo: str, name: str, url: str) -> None:
    subprocess.run(
        ["git", "-C", repo, "remote", "remove", name],
        capture_output=True,
    )
    subprocess.run(["git", "-C", repo, "remote", "add", name, url], check=True)


def fetch_remote_branch(repo: str, remote: str, branch: str) -> str:
    local_ref = _remote_tracking_ref(remote, branch)
    subprocess.run(
        ["git", "-C", repo, "fetch", remote, f"+refs/heads/{branch}:{local_ref}"],
        check=True,
    )
    return local_ref


def checkout_branch_at_ref(repo: str, branch: str, start_ref: str) -> None:
    start_sha = _git(repo, "rev-parse", "--verify", f"{start_ref}^{{commit}}")
    subprocess.run(
        ["git", "-C", repo, "checkout", "-f", "-B", branch, start_sha],
        check=True,
    )


def prepare_translation_branch_on_base(
    repo: str,
    *,
    translation_branch: str,
    base_remote_url: str,
    base_remote_name: str,
    base_branch: str,
    paths: list[str],
) -> None:
    with tempfile.TemporaryDirectory(prefix="ydbdoc-review-staging-") as staging:
        saved: list[str] = []
        root = Path(staging)
        for rel in paths:
            src = Path(repo) / rel.replace("/", os.sep)
            if not src.is_file():
                continue
            dest = root / rel.replace("/", os.sep)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            saved.append(rel)
        ensure_remote(repo, base_remote_name, base_remote_url)
        tip_ref = fetch_remote_branch(repo, base_remote_name, base_branch)
        checkout_branch_at_ref(repo, translation_branch, tip_ref)
        for rel in saved:
            src = root / rel.replace("/", os.sep)
            if not src.is_file():
                continue
            dest = Path(repo) / rel.replace("/", os.sep)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def git_commit_paths(
    repo: str,
    paths: list[str],
    message: str,
    author_name: str,
    author_email: str,
    *,
    all_paths: bool = False,
) -> bool:
    subprocess.run(["git", "-C", repo, "config", "user.name", author_name], check=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", author_email], check=True
    )
    if all_paths:
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    else:
        for rel in paths:
            subprocess.run(["git", "-C", repo, "add", "--", rel], check=True)
    st = subprocess.run(
        ["git", "-C", repo, "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if not (st.stdout or "").strip():
        return False
    subprocess.run(["git", "-C", repo, "commit", "-m", message], check=True)
    return True


def remote_push_url(https_clone_url: str, token: str) -> str:
    parsed = urlparse(https_clone_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"Unexpected remote URL: {https_clone_url}")
    return f"https://x-access-token:{token}@{parsed.hostname}{parsed.path or ''}"


def push_branch(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
) -> None:
    url = remote_push_url(base_https_url, token)
    ensure_remote(repo, remote_name, url)
    proc = subprocess.run(
        ["git", "-C", repo, "push", remote_name, f"HEAD:refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        hint = ""
        if "workflows" in err.lower():
            hint = (
                " Hint: branch may include fork history or workflow changes; "
                "translation branches must be based on upstream base (main), "
                "not the contributor fork. Ensure workflow GITHUB_TOKEN has "
                "contents:write on the upstream repo."
            )
        raise RuntimeError(
            f"git push to {base_https_url} refs/heads/{branch} failed: {err}.{hint}"
        ) from None
