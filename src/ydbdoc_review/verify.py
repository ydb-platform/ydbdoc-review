"""Verify existing PR pairs: fixed RU originals, existing EN, the shared quality loop.

CLI routing/legacy removal is T15. Storage/report adapters use the same RunResult
and hooks as translate. No primary translation or dependency creation occurs here.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from ydbdoc_review.build import build_candidate
from ydbdoc_review.config.loader import Settings, require_actor
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate
from ydbdoc_review.model import ModelChoice, ModelClient
from ydbdoc_review.plan import (
    ChangedFile,
    Plan,
    PlanError,
    PlanOperation,
    Snapshot,
    enforce_limits,
    freeze_snapshot,
    list_changes,
    page_key,
)
from ydbdoc_review.publication import PublicationError, Publisher, freeze
from ydbdoc_review.quality_loop import QualityLoopInterrupted, SelectedFile, run_quality_loop
from ydbdoc_review.runner import RunCancelled, RunHooks, RunResult, finalize


def verification_plan(snapshot: Snapshot, changes: Iterable[ChangedFile],
                      tree: Candidate) -> Plan:
    """Deduplicate changed pairs, including bilingual/renamed pairs at NEW paths.

    Only pairs present in the snapshot qualify. Absent counterparts are not new
    translation work; a claimed existing changed path that cannot be read is an
    error. Plan operations describe possible EN updates, never initial translation.
    """
    plan = Plan(snapshot)

    def read(path):
        try:
            text = tree.text(path)
            if text is None:
                raise ValueError('file absent from snapshot')
            return text
        except Exception as exc:
            raise PlanError(f'Cannot read {path} from {tree.sha}: {exc}') from exc

    for change in changes:
        page = page_key(change.path)
        if page is None or change.kind == 'deleted':
            continue
        read(change.path)
        _, key = page
        source, target = f'ydb/docs/ru/{key}', f'ydb/docs/en/{key}'
        if source in plan.files or source not in tree.entries or target not in tree.entries:
            continue
        source_text = read(source)
        read(target)  # Validate EN readability before model admission, too.
        plan.files[source] = source_text
        plan.operations.append(PlanOperation('modified', 'ru', 'en', source, source,
                                             target, target, True))
    return plan


def run_verify(*, repo: str | Path, github: GitHubClient, owner: str, repository: str,
               pr_number: int, actor: str, settings: Settings, publisher: Publisher,
               model_factory: Callable[[], ModelClient], admit: Callable[[], None],
               critic_choice: ModelChoice, repair_choice: ModelChoice, budget: RequestBudget,
               hooks: RunHooks | None = None, build=build_candidate,
               instruction: str = '', glossary: tuple[tuple[str, str], ...] = ()) -> RunResult:
    """Check/repair and guarded-publish to this open PR, including a no-op receipt.

    Admission happens once after all preflight checks. The transport records all
    critic/repair requests and costs through model_factory's existing callbacks.
    Result files are read from candidate; files (initial translations) stays empty.
    """
    hooks = hooks or RunHooks()
    result = RunResult(mode='doc_verify')
    client = None
    candidate = None
    selected = ()
    expected_head = None
    loop = None
    checked_sha = None
    errors = []
    unfinished = set()
    cancelled = False
    status = 'RED'

    def check_cancel():
        nonlocal cancelled
        if hooks.cancelled and hooks.cancelled():
            cancelled = True
            raise RunCancelled('Run cancelled; available results retained')

    def checked_build(tree):
        check_cancel()
        return build(tree)

    def retain(tree):
        if hooks.candidate_progress:
            try:
                hooks.candidate_progress(tree)
            except Exception as exc:
                errors.append(f'candidate storage: {exc}')

    def commit(previous, updates):
        nonlocal candidate
        candidate = freeze(previous, updates)
        retain(candidate)  # Preserve each immutable result before the next check.
        return candidate

    try:
        require_actor(settings, actor)
        snapshot = freeze_snapshot(github, owner, repository, pr_number)
        result = replace(result, snapshot=snapshot)
        if snapshot.merged:
            raise PlanError('doc_verify currently requires an open PR: same-PR repair publication '
                            'from the merged base snapshot is not implemented')
        pr_repository = f'{owner}/{repository}'
        if (publisher.pr_number != pr_number
                or publisher.repository not in {pr_repository, snapshot.source_repo}
                or publisher.pr_repository not in {None, pr_repository}
                or publisher.branch != snapshot.publication_base):
            raise PublicationError('doc_verify publisher must address the checked PR and its head branch')
        # Keep caller configuration intact. Only a canonical upstream clone URL
        # is redirected to the head repo; malformed/mismatched URLs fail preflight.
        remote_url = publisher.remote_url
        if (publisher.repository == pr_repository and snapshot.source_repo != pr_repository
                and remote_url in {f'https://github.com/{pr_repository}',
                                   f'https://github.com/{pr_repository}.git'}):
            remote_url = f'https://github.com/{snapshot.source_repo}.git'
        publisher = replace(publisher, repository=snapshot.source_repo,
                            remote_url=remote_url, pr_repository=pr_repository)
        tree = Candidate.open(repo, snapshot.source_sha)
        plan = verification_plan(snapshot, list_changes(github, owner, repository, pr_number), tree)
        result = replace(result, plan=plan)
        enforce_limits(plan, settings)
        expected_head = publisher.preflight(snapshot)
        if expected_head != snapshot.source_sha:
            raise PublicationError('PR head changed since the fixed verify snapshot; run doc_verify again')
        if plan.no_work:
            return finalize(replace(result, status='NO_WORK',
                                    message='Нет существующих RU/EN пар для проверки'), hooks)
        selected = tuple(SelectedFile(op.target_new_path, plan.files[op.new_path], 'en',
                                      instruction, glossary) for op in plan.operations)
        check_cancel()
        admit()
        client = model_factory()
        candidate = tree
        unfinished.update(file.path for file in selected)
        retain(candidate)
        loop = run_quality_loop(candidate, selected, client=client, critic_choice=critic_choice,
                                repair_choice=repair_choice, budget=budget, freeze=commit,
                                build=checked_build)
        candidate = loop.candidate
        checked_sha = loop.checked_sha
        unfinished = set(loop.unfinished_files)
        # A technical interruption may leave later documents unvisited, or the
        # last saved repair unverified. Never imply those files are finished.
        if checked_sha != candidate.sha or cancelled:
            unfinished.update(file.path for file in selected)
        status = loop.status
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, QualityLoopInterrupted):
            loop = exc.result
            checked_sha = loop.checked_sha
            unfinished.update(loop.unfinished_files)
        cancelled |= isinstance(exc, (RunCancelled, KeyboardInterrupt))
        errors.append(f'{type(exc).__name__}: {exc}')

    attempts = tuple(client.attempts) if client is not None else ()
    costs = client.cost_breakdown() if client is not None else result.cost_breakdown
    if client is not None:
        try:
            client.close()
        except (Exception, KeyboardInterrupt) as exc:
            cancelled |= isinstance(exc, KeyboardInterrupt)
            errors.append(f'model close: {type(exc).__name__}: {exc}')
    if errors or unfinished or cancelled:
        status = 'RED'
    if status == 'GREEN' and (candidate is None or checked_sha != candidate.sha):
        status = 'RED'
        errors.append('Final candidate does not have a completed exact-SHA check')
    publication = None
    if candidate is not None:
        try:
            publication = publisher.publish(candidate, result.snapshot, expected_head=expected_head,
                                            status=status, checked_sha=checked_sha)
        except PublicationError as exc:
            cancelled |= exc.cancelled or isinstance(exc.__cause__, KeyboardInterrupt)
            publication = exc.publication
            errors.append(f'publication: {exc}')
            status = 'RED'
    result = replace(result, status=status, candidate=candidate, checked_sha=checked_sha,
                     publication=publication, selected_files=selected, quality=loop,
                     issues=loop.issues if loop else (), unfinished_files=tuple(sorted(unfinished)),
                     attempts=attempts, cost_breakdown=costs, errors=tuple(errors), cancelled=cancelled,
                     message='; '.join(errors))
    return finalize(result, hooks, publisher)
