"""Local git operations for the translation workflow."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from ydbdoc_review.pipeline.pairs import ChangeKind


class RefMutationStatus(StrEnum):
    """Outcome proven by one exact ``git push --porcelain`` status row."""

    CHANGED = "changed"
    NOOP = "noop"
    CONFLICT = "conflict"


class RefMutationOperation(StrEnum):
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class RemoteRefLease:
    """Immutable expectation for one destination branch."""

    branch: str
    expected_sha: str | None


@dataclass(frozen=True)
class RefMutationReceipt:
    """Evidence returned by an exact leased remote-ref mutation."""

    lease: RemoteRefLease
    operation: RefMutationOperation
    requested_sha: str | None
    status: RefMutationStatus
    porcelain_flag: str | None
    stdout: str
    stderr: str

    @property
    def owns_requested_state(self) -> bool:
        return self.status is RefMutationStatus.CHANGED


class RemoteRefMutationError(RuntimeError):
    """A leased mutation whose ownership was not proven."""

    def __init__(self, message: str, receipt: RefMutationReceipt) -> None:
        super().__init__(message)
        self.receipt = receipt


class RemoteRefLeaseConflict(RemoteRefMutationError):
    """A leased mutation rejected because the destination ref changed."""


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


def resolve_commit_ref(repo: str, ref: str) -> str:
    """Resolve exactly ``ref`` to a commit SHA without fallback or fetching."""
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def commit_parent_sha(repo: str, commit_sha: str) -> str:
    """Return the first parent of an already-frozen commit."""
    return resolve_commit_ref(repo, f"{commit_sha}^")


def commit_is_ancestor(repo: str, ancestor_sha: str, descendant_sha: str) -> bool:
    """Check one immutable commit-graph relation without moving repository state."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "merge-base",
            "--is-ancestor",
            ancestor_sha,
            descendant_sha,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise RuntimeError(
        "git merge-base --is-ancestor failed for "
        f"{ancestor_sha} -> {descendant_sha}: {(proc.stderr or '').strip()}"
    )


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


def file_diff_between(repo: str, before_sha: str, after_sha: str, rel_path: str) -> str:
    """Return one path diff between two immutable commits."""
    proc = subprocess.run(
        ["git", "-C", repo, "diff", before_sha, after_sha, "--", rel_path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git diff failed for {rel_path}: {err}")
    return proc.stdout or ""


def first_parent_commits_between(
    repo: str,
    start_exclusive_sha: str,
    end_inclusive_sha: str,
) -> tuple[str, ...]:
    """Return the frozen first-parent range ``start..end`` in history order."""
    if not commit_is_ancestor(repo, start_exclusive_sha, end_inclusive_sha):
        return ()
    out = _git(
        repo,
        "rev-list",
        "--first-parent",
        "--reverse",
        f"{start_exclusive_sha}..{end_inclusive_sha}",
    )
    return tuple(line for line in out.splitlines() if line)


def commit_subject(repo: str, commit_sha: str) -> str:
    """Read the subject of one immutable commit."""
    return _git(repo, "show", "-s", "--format=%s", commit_sha)


def _parse_name_status_z(raw: bytes) -> tuple[tuple[str, ChangeKind], ...]:
    """Parse exact paths from ``git diff --name-status -z`` output."""
    if not raw:
        return ()
    if not raw.endswith(b"\0"):
        raise RuntimeError("invalid git name-status output: missing terminal NUL")

    fields = raw[:-1].split(b"\0")

    def decode(field: bytes, *, label: str) -> str:
        try:
            value = field.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"invalid UTF-8 in git name-status {label}") from exc
        if not value:
            raise RuntimeError(f"invalid git name-status output: empty {label}")
        return value

    changes: list[tuple[str, ChangeKind]] = []
    index = 0
    while index < len(fields):
        status = decode(fields[index], label="status")
        index += 1
        status_code = status[0]
        if status_code in {"R", "C"}:
            score = status[1:]
            if not score.isdigit() or int(score) > 100:
                raise RuntimeError(f"invalid git name-status status: {status!r}")
            if index + 1 >= len(fields):
                raise RuntimeError(
                    f"invalid git name-status output: incomplete {status_code} record"
                )
            decode(fields[index], label="source path")
            path = decode(fields[index + 1], label="destination path")
            index += 2
        else:
            if status not in {"A", "B", "D", "M", "T", "U", "X"}:
                raise RuntimeError(f"invalid git name-status status: {status!r}")
            if index >= len(fields):
                raise RuntimeError(
                    f"invalid git name-status output: incomplete {status_code} record"
                )
            path = decode(fields[index], label="path")
            index += 1

        kind: ChangeKind
        if status_code == "A":
            kind = "added"
        elif status_code == "D":
            kind = "deleted"
        else:
            kind = "modified"
        changes.append((path, kind))
    return tuple(changes)


def commit_changes_between(
    repo: str,
    before_sha: str,
    after_sha: str,
) -> tuple[tuple[str, ChangeKind], ...]:
    """Return exact NUL-safe path changes between two immutable commits."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "diff",
            "--name-status",
            "-z",
            before_sha,
            after_sha,
        ],
        capture_output=True,
        text=False,
    )
    if proc.returncode != 0:
        error_bytes = proc.stderr or proc.stdout
        try:
            error = error_bytes.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise RuntimeError("git diff --name-status failed with non-UTF-8 output") from exc
        raise RuntimeError(
            "git diff --name-status failed "
            f"for {before_sha}..{after_sha} (exit {proc.returncode}): "
            f"{error or '(no output)'}"
        )
    return _parse_name_status_z(proc.stdout)


def first_parent_commit_changes(
    repo: str,
    commit_sha: str,
) -> tuple[tuple[str, ChangeKind], ...]:
    """Return paths changed from ``commit^`` to ``commit`` on first parent."""
    parent_sha = commit_parent_sha(repo, commit_sha)
    proc = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "diff",
            "--name-status",
            "-z",
            parent_sha,
            commit_sha,
        ],
        capture_output=True,
        text=False,
    )
    if proc.returncode != 0:
        error_bytes = proc.stderr or proc.stdout
        try:
            error = error_bytes.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise RuntimeError("git diff --name-status failed with non-UTF-8 output") from exc
        raise RuntimeError(
            "git diff --name-status failed "
            f"for {parent_sha}..{commit_sha} (exit {proc.returncode}): "
            f"{error or '(no output)'}"
        )
    return _parse_name_status_z(proc.stdout)


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


def read_text_at_commit(repo: str, commit_sha: str, rel_path: str) -> str | None:
    """Read one UTF-8 blob from an exact commit without consulting the worktree."""
    path = rel_path.replace(os.sep, "/")
    lookup = subprocess.run(
        ["git", "-C", repo, "ls-tree", "--full-tree", "-z", commit_sha, "--", path],
        capture_output=True,
    )
    if lookup.returncode != 0:
        raw_error = lookup.stderr or lookup.stdout or b""
        error = (
            raw_error.decode("utf-8", errors="replace")
            if isinstance(raw_error, bytes)
            else str(raw_error)
        ).strip() or "(no output)"
        raise RuntimeError(
            f"git -C {repo} ls-tree {commit_sha} -- {path} "
            f"failed (exit {lookup.returncode}): {error}"
        )
    if not lookup.stdout:
        return None

    blob = subprocess.run(
        ["git", "-C", repo, "cat-file", "blob", f"{commit_sha}:{path}"],
        capture_output=True,
    )
    if blob.returncode != 0:
        raw_error = blob.stderr or blob.stdout or b""
        error = (
            raw_error.decode("utf-8", errors="replace")
            if isinstance(raw_error, bytes)
            else str(raw_error)
        ).strip() or "(no output)"
        raise RuntimeError(
            f"git -C {repo} cat-file blob {commit_sha}:{path} "
            f"failed (exit {blob.returncode}): {error}"
        )
    try:
        return blob.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"git blob {commit_sha}:{path} is not valid UTF-8"
        ) from exc


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
    base_commit_sha: str,
    deleted_paths: list[str] | None = None,
) -> None:
    start_sha = resolve_commit_ref(repo, base_commit_sha)
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
        checkout_branch_at_ref(repo, translation_branch, start_sha)
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
    staged_diff_args = ["git", "-C", repo, "diff", "--cached", "--quiet"]
    staged_diff = subprocess.run(
        staged_diff_args,
        capture_output=True,
        text=True,
    )
    if staged_diff.returncode == 0:
        return False
    if staged_diff.returncode != 1:
        raise subprocess.CalledProcessError(
            staged_diff.returncode,
            staged_diff_args,
            output=staged_diff.stdout,
            stderr=staged_diff.stderr,
        )
    subprocess.run(["git", "-C", repo, "commit", "-m", message], check=True)
    return True


def remote_push_url(https_clone_url: str, token: str) -> str:
    parsed = urlparse(https_clone_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"Unexpected remote URL: {https_clone_url}")
    return f"https://x-access-token:{token}@{parsed.hostname}{parsed.path or ''}"


def _leased_ref_mutation(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
    *,
    operation: RefMutationOperation,
    requested_sha: str | None,
    expected_remote_sha: str | None,
) -> RefMutationReceipt:
    """Apply one exact CAS mutation and return its porcelain ownership proof."""
    if operation is RefMutationOperation.UPDATE:
        if not requested_sha:
            raise ValueError("an update requires a nonempty candidate commit SHA")
        try:
            requested_sha = resolve_commit_ref(repo, requested_sha)
        except RuntimeError as exc:
            raise RuntimeError(
                f"cannot publish invalid candidate commit SHA {requested_sha!r}"
            ) from exc
    elif requested_sha is not None:
        raise ValueError("a delete must not provide a candidate commit SHA")

    lease = RemoteRefLease(branch=branch, expected_sha=expected_remote_sha)
    if operation is RefMutationOperation.DELETE and expected_remote_sha is None:
        return RefMutationReceipt(
            lease=lease,
            operation=operation,
            requested_sha=None,
            status=RefMutationStatus.NOOP,
            porcelain_flag=None,
            stdout="",
            stderr="",
        )

    url = remote_push_url(base_https_url, token)
    ensure_remote(repo, remote_name, url)
    remote_ref = f"refs/heads/{branch}"
    if expected_remote_sha is not None:
        preserved_ref = (
            f"refs/ydbdoc-review/pre-push/{remote_name}/"
            f"{branch.replace('/', '--')}/{expected_remote_sha}"
        )
        fetched = subprocess.run(
            [
                "git",
                "-C",
                repo,
                "fetch",
                remote_name,
                f"+{remote_ref}:{preserved_ref}",
            ],
            capture_output=True,
            text=True,
        )
        if fetched.returncode != 0:
            stdout = str(fetched.stdout or "")
            stderr = str(fetched.stderr or "")
            receipt = RefMutationReceipt(
                lease=lease,
                operation=operation,
                requested_sha=requested_sha,
                status=RefMutationStatus.CONFLICT,
                porcelain_flag=None,
                stdout=stdout,
                stderr=stderr,
            )
            detail = (stderr or stdout).strip() or "(no output)"
            observed = subprocess.run(
                [
                    "git",
                    "-C",
                    repo,
                    "ls-remote",
                    "--exit-code",
                    remote_name,
                    remote_ref,
                ],
                capture_output=True,
                text=True,
            )
            observed_sha = (
                str(observed.stdout or "").split(maxsplit=1)[0]
                if observed.returncode == 0 and observed.stdout
                else None
            )
            error_type = (
                RemoteRefLeaseConflict
                if observed.returncode == 2
                or (observed_sha is not None and observed_sha != expected_remote_sha)
                else RemoteRefMutationError
            )
            raise error_type(
                f"cannot preserve {remote_ref} before leased mutation: {detail}",
                receipt,
            ) from None
        actual_remote_sha = _git(repo, "rev-parse", preserved_ref)
        if actual_remote_sha != expected_remote_sha:
            receipt = RefMutationReceipt(
                lease=lease,
                operation=operation,
                requested_sha=requested_sha,
                status=RefMutationStatus.CONFLICT,
                porcelain_flag=None,
                stdout=str(fetched.stdout or ""),
                stderr=str(fetched.stderr or ""),
            )
            raise RemoteRefLeaseConflict(
                f"leased destination changed for {remote_ref}: expected "
                f"{expected_remote_sha}, found {actual_remote_sha}",
                receipt,
            )

    refspec = (
        f"{requested_sha}:{remote_ref}"
        if operation is RefMutationOperation.UPDATE
        else f":{remote_ref}"
    )
    proc = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "push",
            "--porcelain",
            f"--force-with-lease={remote_ref}:{expected_remote_sha or ''}",
            remote_name,
            refspec,
        ],
        capture_output=True,
        text=True,
    )
    stdout = str(proc.stdout or "")
    stderr = str(proc.stderr or "")
    matching_rows: list[list[str]] = []
    for line in stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[1] == refspec:
            matching_rows.append(fields)
    raw_flag = matching_rows[0][0] if len(matching_rows) == 1 else None
    stale_lease = bool(
        raw_flag == "!"
        and len(matching_rows) == 1
        and "(stale info)" in matching_rows[0][2]
    )
    flag = raw_flag
    if flag not in {" ", "+", "-", "=", "*"}:
        flag = None

    status = RefMutationStatus.CONFLICT
    if proc.returncode == 0 and len(matching_rows) == 1:
        if operation is RefMutationOperation.DELETE:
            if expected_remote_sha is not None and flag == "-":
                status = RefMutationStatus.CHANGED
        elif flag == "=":
            if expected_remote_sha == requested_sha:
                status = RefMutationStatus.NOOP
        elif expected_remote_sha is None:
            if flag == "*":
                status = RefMutationStatus.CHANGED
        elif expected_remote_sha != requested_sha and flag in {" ", "+"}:
            status = RefMutationStatus.CHANGED

    receipt = RefMutationReceipt(
        lease=lease,
        operation=operation,
        requested_sha=requested_sha,
        status=status,
        porcelain_flag=flag,
        stdout=stdout,
        stderr=stderr,
    )
    if status is RefMutationStatus.CONFLICT:
        detail = "\n".join(
            part.strip() for part in (stdout, stderr) if part.strip()
        ) or "missing or ambiguous porcelain receipt"
        error_type = RemoteRefLeaseConflict if stale_lease else RemoteRefMutationError
        raise error_type(
            f"leased push conflict for {remote_ref}: ownership unconfirmed: {detail}",
            receipt,
        ) from None
    return receipt


def push_branch(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
    *,
    force: bool = False,
    guard_remote_ref: bool = False,
    expected_remote_sha: str | None = None,
    source_sha: str | None = None,
) -> RefMutationReceipt | None:
    """Push ``HEAD`` to ``refs/heads/<branch>`` on the remote.

    ``force`` (§6.166) supports legacy unguarded callers. RED publication sets
    ``guard_remote_ref`` and supplies the exact API snapshot SHA (or ``None``
    for proven absence). Existing remote objects are fetched into a private ref
    both to validate the snapshot and to keep rollback objects locally reachable.
    """
    remote_ref = f"refs/heads/{branch}"
    if guard_remote_ref:
        frozen_source = source_sha or git_head_sha(repo)
        if not frozen_source:
            raise RuntimeError(
                f"cannot publish {remote_ref} without a candidate commit SHA"
            )
        return _leased_ref_mutation(
            repo,
            remote_name,
            branch,
            token,
            base_https_url,
            operation=RefMutationOperation.UPDATE,
            requested_sha=frozen_source,
            expected_remote_sha=expected_remote_sha,
        )
    url = remote_push_url(base_https_url, token)
    ensure_remote(repo, remote_name, url)
    cmd = ["git", "-C", repo, "push"]
    if force:
        cmd.append("--force")
    cmd.extend([remote_name, f"{source_sha or 'HEAD'}:{remote_ref}"])
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
    return None


def delete_remote_branch_with_lease(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
    *,
    expected_remote_sha: str,
) -> RefMutationReceipt:
    """Delete a present branch only while it still has the frozen SHA."""
    if not expected_remote_sha:
        raise ValueError("leased branch deletion requires a nonempty expected SHA")
    return _leased_ref_mutation(
        repo,
        remote_name,
        branch,
        token,
        base_https_url,
        operation=RefMutationOperation.DELETE,
        requested_sha=None,
        expected_remote_sha=expected_remote_sha,
    )


def rollback_pushed_branch(
    repo: str,
    remote_name: str,
    branch: str,
    token: str,
    base_https_url: str,
    *,
    expected_pushed_sha: str | None,
    previous_sha: str | None,
) -> RefMutationReceipt:
    """Restore/delete a just-pushed ref only while it still has our exact SHA."""
    if previous_sha is None and expected_pushed_sha is None:
        raise ValueError("rollback requires an owned changed state")
    try:
        return _leased_ref_mutation(
            repo,
            remote_name,
            branch,
            token,
            base_https_url,
            operation=(
                RefMutationOperation.UPDATE
                if previous_sha is not None
                else RefMutationOperation.DELETE
            ),
            requested_sha=previous_sha,
            expected_remote_sha=expected_pushed_sha,
        )
    except RemoteRefMutationError as exc:
        raise RuntimeError(
            f"refusing to roll back refs/heads/{branch}: guarded lease failed: {exc}"
        ) from exc
