from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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


def file_diff_between_refs(repo: str, ref_a: str, ref_b: str, rel_path: str) -> str:
    """Unified diff for *rel_path* between two commits/refs (``git diff ref_a ref_b -- path``)."""
    p = subprocess.run(
        ["git", "-C", repo, "diff", ref_a, ref_b, "--", rel_path],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        err = (p.stderr or "").strip() or (p.stdout or "").strip() or "(no output)"
        raise RuntimeError(
            f"git -C {repo} diff {ref_a} {ref_b} -- {rel_path} failed "
            f"(exit {p.returncode}): {err}"
        )
    return p.stdout


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


def commits_touching_path(
    repo: str,
    ref: str,
    rel_path: str,
    *,
    max_count: int = 50,
) -> list[str]:
    """SHAs of commits on `ref` that touched `rel_path`, newest first."""
    path = rel_path.replace(os.sep, "/")
    limit = str(max(1, max_count))
    p = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "log",
            ref,
            f"-{limit}",
            "--format=%H",
            "--",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return []
    return [line.strip() for line in (p.stdout or "").splitlines() if line.strip()]


def first_commit_for_path(repo: str, ref: str, rel_path: str) -> str | None:
    """
    SHA of the earliest commit on `ref` that introduced or touched `rel_path`.
    Prefer --diff-filter=A (file add); fall back to oldest commit in history.
    """
    path = rel_path.replace(os.sep, "/")
    for extra in (["--diff-filter=A"], []):
        p = subprocess.run(
            [
                "git",
                "-C",
                repo,
                "log",
                ref,
                "-1",
                "--format=%H",
                *extra,
                "--",
                path,
            ],
            capture_output=True,
            text=True,
        )
        if p.returncode == 0:
            sha = (p.stdout or "").strip()
            if sha:
                return sha
    p = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "log",
            ref,
            "--reverse",
            "-1",
            "--format=%H",
            "--",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        sha = (p.stdout or "").strip()
        if sha:
            return sha
    return None


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


def _remote_tracking_ref(remote: str, branch: str) -> str:
    """
    Local ref for ``refs/heads/{branch}`` after fetch.

    Branch names like ``ydbdoc-review/pr-39815`` cannot be stored as
    ``refs/remotes/{remote}/ydbdoc-review/pr-39815`` — Git treats the middle
    segment as a directory and checkout fails. Slashes are flattened to ``--``.
    """
    safe = branch.replace("/", "--")
    return f"refs/remotes/{remote}/{safe}"


def fetch_remote_branch(repo: str, remote: str, branch: str) -> str:
    """Fetch one branch from remote; return a local commit ref suitable for checkout."""
    local_ref = _remote_tracking_ref(remote, branch)
    subprocess.run(
        ["git", "-C", repo, "fetch", remote, f"+refs/heads/{branch}:{local_ref}"],
        check=True,
    )
    return local_ref


def resolve_commit(repo: str, ref: str) -> str:
    """Return full commit SHA for ``ref`` (branch, remote-tracking ref, etc.)."""
    sha = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not sha:
        raise RuntimeError(f"Not a commit: {ref}")
    return sha


def checkout_branch_at_ref(repo: str, branch: str, start_ref: str) -> None:
    """Create/reset ``branch`` at ``start_ref``. ``-f`` drops local edits (caller restores via snapshot)."""
    start_sha = resolve_commit(repo, start_ref)
    subprocess.run(
        ["git", "-C", repo, "checkout", "-f", "-B", branch, start_sha],
        check=True,
    )


def _snapshot_paths_to_dir(repo: str, paths: list[str], staging_dir: str) -> list[str]:
    """Copy paths to a temp dir (text + binary) before branch checkout."""
    saved: list[str] = []
    root = Path(staging_dir)
    for rel in paths:
        src = Path(repo) / rel.replace("/", os.sep)
        if not src.is_file():
            continue
        dest = root / rel.replace("/", os.sep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        saved.append(rel)
    return saved


def _restore_paths_from_dir(repo: str, staging_dir: str, paths: list[str]) -> None:
    root = Path(staging_dir)
    for rel in paths:
        src = root / rel.replace("/", os.sep)
        if not src.is_file():
            continue
        dest = Path(repo) / rel.replace("/", os.sep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


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
    Move uncommitted translation files onto a fresh branch tip for publishing.

    Always resets the translation branch to ``base_branch`` from ``base_remote_name``
    (re-fetch after ``ensure_remote`` so an earlier remote-tracking ref is not stale).
    """
    with tempfile.TemporaryDirectory(prefix="ydbdoc-review-staging-") as staging:
        saved = _snapshot_paths_to_dir(repo, paths, staging)
        ensure_remote(repo, base_remote_name, base_remote_url)
        tip_ref = fetch_remote_branch(repo, base_remote_name, base_branch)
        checkout_branch_at_ref(repo, translation_branch, tip_ref)
        _restore_paths_from_dir(repo, staging, saved)


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


def read_text_at_ref(repo: str, ref: str, rel_path: str) -> str | None:
    """UTF-8 file contents at `ref:path`, or None."""
    path = rel_path.replace(os.sep, "/")
    p = subprocess.run(
        ["git", "-C", repo, "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return None
    return p.stdout


def copy_file_in_repo(repo: str, src_rel: str, dest_rel: str) -> bool:
    """Copy a file within the repo working tree (binary-safe)."""
    src = Path(repo) / src_rel.replace("/", os.sep)
    dest = Path(repo) / dest_rel.replace("/", os.sep)
    if not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


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
    return git_commit_paths(repo, [], message, author_name, author_email, all_paths=True)


def git_commit_paths(
    repo: str,
    paths: list[str],
    message: str,
    author_name: str,
    author_email: str,
    *,
    all_paths: bool = False,
) -> bool:
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", author_name],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", author_email],
        check=True,
    )
    if all_paths:
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    else:
        for rel in paths:
            subprocess.run(["git", "-C", repo, "add", "--", rel], check=True)
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


def push_branch(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
    *,
    force_with_lease: bool = False,
    force: bool = False,
) -> None:
    url = remote_push_url(base_https_url, token)
    subprocess.run(
        ["git", "-C", repo, "remote", "remove", remote_name],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo, "remote", "add", remote_name, url],
        check=True,
    )
    push_args = ["git", "-C", repo, "push", remote_name, f"HEAD:refs/heads/{branch}"]
    if force:
        push_args.insert(4, "--force")
    elif force_with_lease:
        push_args.insert(4, "--force-with-lease")
    subprocess.run(push_args, check=True)


def try_push_branch(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
    *,
    force_with_lease: bool = False,
    force: bool = False,
) -> str | None:
    """Push ``HEAD`` to ``branch``; return ``None`` on success or a short error message."""
    try:
        push_branch(
            repo,
            remote_name,
            branch,
            token,
            base_https_url,
            force_with_lease=force_with_lease,
            force=force,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or b"").decode(errors="replace").strip()
        msg = f"exit {exc.returncode}"
        if detail:
            msg = f"{msg}: {detail.splitlines()[-1] if detail else detail}"
        return msg
    return None


def try_fetch_remote_branch(repo: str, remote_name: str, branch: str) -> str | None:
    """Fetch ``branch`` from ``remote_name``; return local ref or None if missing."""
    local_ref = _remote_tracking_ref(remote_name, branch)
    p = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "fetch",
            remote_name,
            f"+refs/heads/{branch}:{local_ref}",
        ],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return None
    verify = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", f"{local_ref}^{{commit}}"],
        capture_output=True,
    )
    if verify.returncode != 0:
        return None
    return local_ref
