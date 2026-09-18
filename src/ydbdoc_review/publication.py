"""Immutable Git candidates and a single guarded publication, shared by modes."""
from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.git_ops import RefMutationStatus, push_branch
from ydbdoc_review.links import Candidate, safe_path
from ydbdoc_review.plan import Snapshot


def freeze(previous: Candidate, updates: Mapping[str, bytes | None]) -> Candidate:
    """Commit exact updates over previous SHA; never touch worktree or real index.

    This is also the T11/T13 quality-loop freeze callback. No-op returns previous.
    """
    changes = {safe_path(p): data for p, data in updates.items() if previous.read(p) != data}
    if not changes:
        return previous
    with tempfile.TemporaryDirectory(prefix='ydbdoc-index-') as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / 'index'))
        def git(*args, data=None):
            result = subprocess.run(['git', '-C', str(previous.repo), *args], input=data,
                                    capture_output=True, env=env, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace'))
            return result.stdout.strip().decode()
        git('read-tree', previous.sha)
        for path, data in changes.items():
            if data is None:
                git('update-index', '--force-remove', '--', path)
            else:
                oid = git('hash-object', '-w', '--stdin', data=data)
                mode = previous.entries.get(path, ('100644', ''))[0]
                git('update-index', '--add', '--cacheinfo', f'{mode},{oid},{path}')
        tree = git('write-tree')
        sha = git('-c', 'user.name=YDB Docs', '-c', 'user.email=ydbdoc@users.noreply.github.com',
                  'commit-tree', tree, '-p', previous.sha, data=b'Documentation translation\n')
    return Candidate.open(previous.repo, sha)


@dataclass(frozen=True)
class Publication:
    repository: str
    branch: str
    base: str
    pushed_sha: str
    pr_number: int | None = None
    url: str | None = None
    draft: bool = True
    head_confirmed: bool = False


class PublicationError(RuntimeError):
    def __init__(self, message: str, publication: Publication | None = None, *,
                 cancelled: bool = False):
        super().__init__(message)
        self.publication = publication
        self.cancelled = cancelled


def confirm_draft(github: GitHubClient, publication: Publication) -> Publication:
    """Change PR metadata and return draft=True only after GitHub confirms it."""
    owner, repo = publication.repository.split('/', 1)
    github.convert_pull_to_draft(owner, repo, publication.pr_number)
    actual = github.get_pull(owner, repo, publication.pr_number)
    if not actual.get('draft'):
        raise RuntimeError('Draft state not confirmed')
    return replace(publication, draft=True)


@dataclass
class Publisher:
    """Explicit repository identity prevents silently publishing a fork on main.

    repository/remote_url identify the push destination. pr_repository optionally
    identifies an existing upstream PR whose head lives in a fork; new translation
    PRs retain the original same-repository semantics.
    expected_head is frozen BEFORE paid work; None means proven absence.
    Existing PR modes pass pr_number and their exact expected_head.
    """
    github: GitHubClient
    repository: str
    remote_url: str
    branch: str
    token: str = ''
    pr_number: int | None = None
    base: str | None = None
    pr_repository: str | None = None

    def preflight(self, snapshot: Snapshot) -> str | None:
        if self.pr_repository is not None and self.pr_number is None:
            raise PublicationError('Separate PR repository requires an existing PR')
        if self.repository != snapshot.source_repo:
            raise PublicationError(f'Publication repository must be {snapshot.source_repo}; '
                                   f'got {self.repository}. Cannot redirect fork to main.')
        url = urlsplit(self.remote_url)
        if (url.scheme != 'https' or url.netloc != 'github.com'
                or url.path.removesuffix('.git').strip('/') != self.repository):
            raise PublicationError('Clone URL does not match publication repository')
        if self.pr_number is None and self.base not in {None, snapshot.publication_base}:
            raise PublicationError('Translation base differs from source snapshot')
        if self.branch == snapshot.publication_base and self.pr_number is None:
            raise PublicationError('Translation branch must differ from its base')
        result = subprocess.run(['git', 'check-ref-format', 'refs/heads/' + self.branch],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise PublicationError('Invalid publication branch')
        owner, repo = self.repository.split('/', 1)
        expected = self.github.get_branch_sha(owner, repo, self.branch)
        if self.pr_number is None and expected is not None:
            raise PublicationError('New translation branch already exists; choose a new run branch')
        if self.pr_number is not None:
            pr_owner, pr_repo = (self.pr_repository or self.repository).split('/', 1)
            pull = self.github.get_pull(pr_owner, pr_repo, self.pr_number)
            if (pull['head']['ref'] != self.branch or pull['head']['sha'] != expected
                    or pull['head']['repo']['full_name'] != self.repository):
                raise PublicationError('Existing PR head differs from expected publication head')
            if self.base is not None and self.base != pull['base']['ref']:
                raise PublicationError('Existing PR base differs from expected base')
            self.base = pull['base']['ref']
        return expected

    def publish(self, candidate: Candidate, snapshot: Snapshot, *, expected_head: str | None,
                status: str, checked_sha: str | None, report_result=None) -> Publication:
        pr_repository = self.pr_repository or self.repository
        owner, repo = pr_repository.split('/', 1)
        base = self.base or snapshot.publication_base
        green = status == 'GREEN' and checked_sha == candidate.sha
        publication = None
        try:
            receipt = push_branch(str(candidate.repo), 'ydbdoc-publication', self.branch,
                                  self.token, self.remote_url, guard_remote_ref=True,
                                  expected_remote_sha=expected_head, source_sha=candidate.sha)
            if receipt is None or not (receipt.owns_requested_state or (
                    receipt.status is RefMutationStatus.NOOP and expected_head == candidate.sha)):
                raise RuntimeError('Push ownership was not confirmed')
            publication = Publication(pr_repository, self.branch, base,
                                      candidate.sha, self.pr_number, draft=False)
            if self.pr_number is None:
                from ydbdoc_review.report import initial_description
                from ydbdoc_review.runner import RunResult
                creation_result = report_result or RunResult(snapshot=snapshot, candidate=candidate,
                    status=status, checked_sha=checked_sha)
                description = initial_description(creation_result, secrets=(self.token,))
                created = self.github.create_pull(
                    owner, repo, title=f'Documentation translation #{snapshot.pr_number}',
                    head=self.branch, base=base,
                    body=description, draft=not green)
                if not created:
                    raise RuntimeError('GitHub did not confirm PR creation')
                url, number, _ = created
            else:
                number = self.pr_number
                url = f'https://github.com/{pr_repository}/pull/{number}'
            publication = Publication(pr_repository, self.branch, base,
                                      candidate.sha, number, url, draft=False)
            # create_pull may have returned an existing ready PR after a race.
            actual = self.github.get_pull(owner, repo, number)
            if not green and not actual.get('draft'):
                self.github.convert_pull_to_draft(owner, repo, number)
                actual = self.github.get_pull(owner, repo, number)
                if not actual.get('draft'):
                    raise RuntimeError('GitHub did not confirm draft state')
            if (actual['head']['sha'] != candidate.sha
                    or actual['head']['ref'] != self.branch
                    or actual['head']['repo']['full_name'] != self.repository
                    or actual['base']['ref'] != base):
                raise RuntimeError('Published PR changed; its current head is not the checked candidate')
            return Publication(pr_repository, self.branch, base, candidate.sha, number,
                               url, bool(actual.get('draft')), True)
        except (Exception, KeyboardInterrupt) as exc:
            detail = f'{type(exc).__name__}: {exc}'
            cancelled = isinstance(exc, KeyboardInterrupt)
            if publication and publication.pr_number:
                try:
                    publication = confirm_draft(self.github, publication)
                except (Exception, KeyboardInterrupt) as draft_error:
                    cancelled |= isinstance(draft_error, KeyboardInterrupt)
                    detail += f'; draft: {type(draft_error).__name__}: {draft_error}'
            raise PublicationError(detail, publication, cancelled=cancelled) from exc
