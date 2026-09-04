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


def ensure_commit(repo: str, sha: str) -> bool:
    """Make ``sha`` resolvable locally (fetch from ``origin`` if needed)."""
    if not sha:
        return False
    probe = subprocess.run(
        ["git", "-C", repo, "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return True
    fetch = subprocess.run(
        ["git", "-C", repo, "fetch", "--no-tags", "origin", sha],
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        return False
    probe = subprocess.run(
        ["git", "-C", repo, "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


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


def upstream_ref_candidates(merge_base_with: str) -> list[str]:
    """Ref spellings for the translation-branch tip (usually ``origin/main``)."""
    candidates: list[str] = [merge_base_with]
    if merge_base_with.startswith("origin/"):
        branch = merge_base_with[len("origin/") :]
        candidates.extend(
            (
                f"refs/remotes/origin/{branch}",
                branch,
            )
        )
    elif "/" not in merge_base_with:
        candidates.append(f"origin/{merge_base_with}")
        candidates.append(f"refs/remotes/origin/{merge_base_with}")
    out: list[str] = []
    seen: set[str] = set()
    for ref in candidates:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def read_text_at_upstream_tip(
    repo: str, merge_base_with: str, rel_path: str
) -> str | None:
    """Read a path from the upstream tip used as the translation-branch base.

    Prefer ``origin/main`` (etc.) over ``merge-base(HEAD, main)``. For merged
    source PRs HEAD is often an ancestor of main, so merge-base == HEAD and EN
    sidebars look falsely up-to-date (§6.140 / #48018).
    """
    for ref in upstream_ref_candidates(merge_base_with):
        text = read_text_at_ref(repo, ref, rel_path)
        if text is not None:
            return text
    return None


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
    deleted_paths: list[str] | None = None,
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
        for rel in deleted_paths or []:
            dest = Path(repo) / rel.replace("/", os.sep)
            if dest.is_file():
                dest.unlink()


def git_commit_paths(
    repo: str,
    paths: list[str],
    message: str,
    author_name: str,
    author_email: str,
    *,
    deleted_paths: list[str] | None = None,
    all_paths: bool = False,
) -> bool:
    subprocess.run(["git", "-C", repo, "config", "user.name", author_name], check=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", author_email], check=True
    )
    if all_paths:
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    else:
        for rel in deleted_paths or []:
            subprocess.run(
                ["git", "-C", repo, "rm", "--ignore-unmatch", "--", rel],
                check=True,
            )
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
    *,
    force: bool = False,
) -> None:
    """Push ``HEAD`` to ``refs/heads/<branch>`` on the remote.

    ``force`` (§6.166): re-``doc_translate`` rebuilds ``ydbdoc-review/pr-*``
    from upstream main. A plain push is non-fast-forward when a previous
    translate/verify tip still exists. Use plain ``--force`` (not
    ``--force-with-lease``): the action checkout never fetches the remote
    translation tip, so lease checks fail with ``(stale info)``.
    """
    url = remote_push_url(base_https_url, token)
    ensure_remote(repo, remote_name, url)
    cmd = ["git", "-C", repo, "push"]
    if force:
        cmd.append("--force")
    cmd.extend([remote_name, f"HEAD:refs/heads/{branch}"])
    proc = subprocess.run(
        cmd,
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


def rollback_pushed_branch(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
    *,
    expected_pushed_sha: str,
    previous_sha: str | None,
) -> None:
    """Restore/delete a just-pushed ref only while it still has our exact SHA."""
    url = remote_push_url(base_https_url, token)
    ensure_remote(repo, remote_name, url)
    ref = f"refs/heads/{branch}"
    refspec = f"{previous_sha}:{ref}" if previous_sha else f":{ref}"
    proc = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "push",
            f"--force-with-lease={ref}:{expected_pushed_sha}",
            remote_name,
            refspec,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"refusing to roll back {ref}: guarded lease failed: {err}"
        ) from None
