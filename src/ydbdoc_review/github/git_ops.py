"""Local git operations for the translation workflow."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class RefMutationStatus(StrEnum):
    """Outcome proven by one exact ``git push --porcelain`` status row."""

    CHANGED = "changed"
    NOOP = "noop"
    CONFLICT = "conflict"


class RefMutationOperation(StrEnum):
    UPDATE = "update"


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


def resolve_commit_ref(repo: str, ref: str) -> str:
    """Resolve exactly ``ref`` to a commit SHA without fallback or fetching."""
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


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


def ensure_remote(repo: str, name: str, url: str) -> None:
    subprocess.run(
        ["git", "-C", repo, "remote", "remove", name],
        capture_output=True,
    )
    subprocess.run(["git", "-C", repo, "remote", "add", name, url], check=True)


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
    if not requested_sha:
        raise ValueError("an update requires a nonempty candidate commit SHA")
    requested_sha = resolve_commit_ref(repo, requested_sha)
    lease = RemoteRefLease(branch=branch, expected_sha=expected_remote_sha)

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

    refspec = f"{requested_sha}:{remote_ref}"
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
        if flag == "=":
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
    repo: str, remote_name: str, branch: str, token: str, base_https_url: str,
    *, guard_remote_ref: bool = True, expected_remote_sha: str | None = None,
    source_sha: str,
) -> RefMutationReceipt:
    """Publish an exact candidate with mandatory compare-and-swap protection."""
    if not guard_remote_ref:
        raise ValueError("Publication requires a remote lease")
    return _leased_ref_mutation(
        repo, remote_name, branch, token, base_https_url,
        operation=RefMutationOperation.UPDATE, requested_sha=source_sha,
        expected_remote_sha=expected_remote_sha,
    )
