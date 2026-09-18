"""Translate entrypoint. CLI wiring is T15; persistence/report adapters are T12/T14.

Hooks receive real immutable results, including RED/cancel/failed publication.
There is no workflow registry, legacy harness, or alternate quality policy.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import MappingProxyType

from ydbdoc_review.build import automatic_ok, build_candidate
from ydbdoc_review.config.loader import Settings, require_actor
from ydbdoc_review.diagnostics import redact_known
from ydbdoc_review.document import FileResult, RequestBudget, translate_document
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import (
    Candidate,
    check_links,
    confirmed_english_url,
    prepare_assets,
    references,
)
from ydbdoc_review.model import AttemptRecord, ModelChoice, ModelClient
from ydbdoc_review.plan import (
    Plan,
    Snapshot,
    freeze_snapshot,
    list_changes,
    prepare_translation_plan,
    read_at_sha,
)
from ydbdoc_review.publication import (
    Publication,
    PublicationError,
    Publisher,
    confirm_draft,
    freeze,
)
from ydbdoc_review.quality import Issue, deterministic_checks
from ydbdoc_review.quality_loop import LoopResult, SelectedFile, run_quality_loop


@dataclass(frozen=True)
class RunResult:
    mode: str = 'doc_translate'
    status: str = 'RED'
    message: str = ''
    snapshot: Snapshot | None = None
    plan: Plan | None = None
    candidate: Candidate | None = None
    checked_sha: str | None = None
    publication: Publication | None = None
    files: tuple[FileResult, ...] = ()
    selected_files: tuple[SelectedFile, ...] = ()
    issues: tuple[Issue, ...] = ()
    unfinished_files: tuple[str, ...] = ()
    quality: LoopResult | None = None
    attempts: tuple[AttemptRecord, ...] = ()
    cost_breakdown: Mapping[str, Decimal | None] = field(default_factory=lambda: {
        name: Decimal(0) for name in ('translation', 'critic', 'repair', 'total')})
    errors: tuple[str, ...] = ()
    cancelled: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'cost_breakdown', MappingProxyType(dict(self.cost_breakdown)))

    @property
    def candidate_sha(self) -> str | None:
        return self.candidate.sha if self.candidate else None

    @property
    def result_sha(self) -> str | None:
        return (self.publication.pushed_sha
                if self.publication and self.publication.head_confirmed else None)


@dataclass(frozen=True)
class RunHooks:
    """T12 saves progress/context; T14 renders the same finalized RunResult.

    model_factory owns request/attempt recording callbacks. save runs before report;
    failures are explicit in the returned result and never skip publication.
    cancelled is cooperative; KeyboardInterrupt also uses the same finalization.
    """
    file_progress: Callable[[FileResult], None] | None = None
    candidate_progress: Callable[[Candidate], None] | None = None
    save: Callable[[RunResult], None] | None = None
    report: Callable[[RunResult], None] | None = None
    cancelled: Callable[[], bool] | None = None
    secrets: tuple[str, ...] = ()


class RunCancelled(Exception):
    pass


def finalize(result: RunResult, hooks: RunHooks, publisher: Publisher | None = None) -> RunResult:
    """Finalize metadata only; save before report, reconcile changed outcome once.

    Every boundary can turn a published GREEN into RED. Reconcile draft before
    handing off that result and after each callback, including interruptions.
    A failed draft conversion is explicit, never a claim of confirmed draft.
    """
    # Sanitize only public summary/errors; raw requests and responses stay intact.
    message = redact_known(result.message, hooks.secrets)
    errors = tuple(redact_known(error, hooks.secrets) for error in result.errors)
    if message != result.message or errors != result.errors:
        result = replace(result, message=message, errors=errors)
    draft_attempted = False

    def failed(name, exc):
        nonlocal result
        error = redact_known(f'{name}: {type(exc).__name__}: {exc}', hooks.secrets)
        result = replace(result, status='RED', errors=(*result.errors, error),
                         message='; '.join(filter(None, (result.message, error))),
                         cancelled=result.cancelled or isinstance(exc, (RunCancelled, KeyboardInterrupt)))

    def reconcile():
        nonlocal result, draft_attempted
        if hooks.cancelled and not result.cancelled:
            try:
                if hooks.cancelled():
                    raise RunCancelled('Run cancelled; available results retained')
            except (Exception, KeyboardInterrupt) as exc:
                failed('cancellation', exc)
        publication = result.publication
        if (result.status == 'RED' and publication and publication.pr_number
                and not publication.draft and not draft_attempted):
            draft_attempted = True
            try:
                if publisher is None:
                    raise RuntimeError('Publisher unavailable; draft state not confirmed')
                result = replace(result, publication=confirm_draft(publisher.github, publication))
            except (Exception, KeyboardInterrupt) as exc:
                failed('draft', exc)

    reconcile()
    saved_result = None
    for name, callback in (('storage', hooks.save), ('report', hooks.report)):
        if callback:
            try:
                if name == 'storage':
                    saved_result = result
                callback(result)
            except (Exception, KeyboardInterrupt) as exc:
                failed(name, exc)
            reconcile()
    # A late report/cancellation/draft failure must not leave a stored GREEN.
    # One bounded reconciliation write, no second report or quality/model call.
    if hooks.save and saved_result is not result:
        try:
            hooks.save(result)
        except (Exception, KeyboardInterrupt) as exc:
            failed('storage final outcome', exc)
            reconcile()
    return result


def run_translate(*, repo: str | Path, github: GitHubClient, owner: str, repository: str,
                  pr_number: int, actor: str, settings: Settings, publisher: Publisher,
                  model_factory: Callable[[], ModelClient], admit: Callable[[], None],
                  translation_choice: ModelChoice, critic_choice: ModelChoice,
                  repair_choice: ModelChoice, budget: RequestBudget,
                  hooks: RunHooks | None = None, build=build_candidate,
                  instruction: str = '', glossary: tuple[tuple[str, str], ...] = (),
                  snapshot: Snapshot | None = None) -> RunResult:
    """Run against locally available source commit objects (fetch is CLI setup).

    A supplied snapshot is the exact commit already fetched by CLI; direct callers
    may omit it to freeze once here.
    admit raises on exhausted daily budget/storage error, exactly once before model
    creation. Mechanical/no-work/preflight paths neither admit nor create a client.
    publisher.preflight captures expected head before paid work, never refreshes it.
    """
    hooks = hooks or RunHooks()
    result = RunResult()
    client = None
    plan = None
    candidate = None
    initial_tree = None
    files: dict[str, FileResult] = {}
    unfinished: set[str] = set()
    expected_head = None
    admitted = False
    errors = []
    issues = []
    selected = ()
    loop = None
    checked_sha = None
    status = 'RED'
    cancelled = False

    def check_cancel():
        nonlocal cancelled
        if hooks.cancelled and hooks.cancelled():
            cancelled = True
            raise RunCancelled('Run cancelled; available results retained')

    def checked_build(tree):
        check_cancel()
        return build(tree)

    def progress(file):
        files[file.path] = file  # retain BEFORE user callback/cancellation
        if file.unfinished or file.text is None:
            unfinished.add(file.path)
        else:
            unfinished.discard(file.path)
        if hooks.file_progress:
            hooks.file_progress(file)
        check_cancel()

    def commit(previous, updates):
        nonlocal candidate
        candidate = freeze(previous, updates)
        if hooks.candidate_progress:
            try:
                hooks.candidate_progress(candidate)
            except Exception as exc:
                errors.append(f'candidate storage: {exc}')
        return candidate

    try:
        require_actor(settings, actor)
        snapshot = snapshot or freeze_snapshot(github, owner, repository, pr_number)
        result = replace(result, snapshot=snapshot)
        changes = list_changes(github, owner, repository, pr_number)
        plan = prepare_translation_plan(snapshot, changes, partial(read_at_sha, str(repo)), settings)
        result = replace(result, plan=plan)
        if plan.no_work:
            return finalize(replace(result, status='NO_WORK', message='Перевод не требуется'), hooks)
        initial_tree = Candidate.open(repo, snapshot.source_sha)
        expected_head = publisher.preflight(snapshot)
        # Validate all mechanical inputs before admitting or creating any candidate.
        updates = {}
        for op in plan.operations:
            if op.kind == 'deleted':
                updates[op.target_old_path] = None
            elif op.kind == 'renamed':
                data = initial_tree.read(op.target_old_path)
                if data is None and not op.needs_translation:
                    raise ValueError(f'Missing translation for pure rename: {op.target_old_path}')
                if (op.target_new_path != op.target_old_path
                        and initial_tree.read(op.target_new_path) is not None):
                    raise ValueError(f'Rename destination already exists: {op.target_new_path}')
                updates[op.target_old_path] = None
                if data is not None:
                    updates[op.target_new_path] = data
        unfinished = {op.target_new_path for op in plan.operations if op.needs_translation}
        check_cancel()
        if plan.needs_model:
            admit()
            admitted = True
            client = model_factory()
            for op in plan.operations:
                if not op.needs_translation:
                    continue
                check_cancel()
                file = translate_document(plan.files[op.new_path], path=op.target_new_path,
                                          source_lang=op.source_language,
                                          target_lang=op.target_language, client=client,
                                          choice=translation_choice, budget=budget,
                                          on_progress=progress, glossary=dict(glossary))
                progress(file)
        else:
            admitted = True  # mechanical work requires no paid admission
    except (Exception, KeyboardInterrupt) as exc:
        cancelled = isinstance(exc, (RunCancelled, KeyboardInterrupt))
        errors.append(f'{type(exc).__name__}: {exc}')

    # One finalization path: cancellation/errors never discard received text/assets.
    if plan is not None and initial_tree is not None and admitted:
        try:
            pairs = {}
            for op in plan.operations:
                file = files.get(op.target_new_path)
                if file is not None and file.text is not None:
                    updates[op.target_new_path] = file.text.encode('utf-8')
                    pairs[op.new_path] = op.target_new_path
                    if op.kind == 'renamed':
                        updates[op.target_old_path] = None
                elif op.kind == 'renamed' and updates.get(op.target_new_path) is not None:
                    pairs[op.new_path] = op.target_new_path
            assets = prepare_assets(initial_tree, pairs, overrides=updates)
            issues.extend(assets.issues)
            updates.update(assets.copies)
            candidate = commit(initial_tree, updates)
            selected = tuple(SelectedFile(op.target_new_path, plan.files[op.new_path],
                                          op.target_language, instruction, glossary,
                                          files[op.target_new_path])
                             for op in plan.operations if op.needs_translation
                             and op.target_new_path in files
                             and files[op.target_new_path].text is not None)
            if not errors:
                check_cancel()
                if plan.needs_model:
                    loop = run_quality_loop(candidate, selected, client=client,
                                            critic_choice=critic_choice, repair_choice=repair_choice,
                                            budget=budget, freeze=commit, build=checked_build)
                    candidate = loop.candidate
                    checked_sha = loop.checked_sha
                    issues.extend(loop.issues)
                    # Successful repair may finish an initially partial document.
                    unfinished.difference_update(f.path for f in selected)
                    unfinished.update(loop.unfinished_files)
                    status = loop.status
                else:
                    built = build(candidate)
                    links = check_links(candidate, build=built)
                    issues.extend(built.issues_for(candidate.sha))
                    issues.extend(links.issues)
                    for op in plan.operations:
                        if op.kind != 'renamed':
                            continue
                        source = plan.files[op.new_path]
                        replacements = {}
                        for ref in references(source):
                            try:
                                replacements[ref.href] = confirmed_english_url(
                                    candidate, op.target_new_path, ref.href, build=built)
                            except ValueError:
                                pass  # whole-tree links supplies the concrete error
                        issues.extend(deterministic_checks(
                            source, candidate.text(op.target_new_path), path=op.target_new_path,
                            target_lang=op.target_language, glossary=dict(glossary),
                            validated_url_replacements=replacements))
                    checked_sha = candidate.sha
                    status = 'GREEN' if automatic_ok(candidate.sha, links, built) else 'RED'
        except (Exception, KeyboardInterrupt) as exc:
            cancelled |= isinstance(exc, (RunCancelled, KeyboardInterrupt))
            errors.append(f'{type(exc).__name__}: {exc}')

    if errors or unfinished or any(i.severity == 'error' for i in issues):
        status = 'RED'
    if status == 'GREEN' and (candidate is None or checked_sha != candidate.sha):
        status = 'RED'
        errors.append('Final candidate does not have a completed exact-SHA check')
    publication = None
    if candidate is not None and initial_tree is not None and candidate.sha != initial_tree.sha:
        try:
            publication = publisher.publish(candidate, plan.snapshot, expected_head=expected_head,
                                            status=status, checked_sha=checked_sha,
                                            report_result=replace(result, status=status, candidate=candidate,
                                                checked_sha=checked_sha, files=tuple(files.values()),
                                                selected_files=selected, issues=tuple(issues), quality=loop,
                                                unfinished_files=tuple(sorted(unfinished)), errors=tuple(errors),
                                                cost_breakdown=client.cost_breakdown() if client else result.cost_breakdown))
        except PublicationError as exc:
            cancelled |= exc.cancelled
            publication = exc.publication
            errors.append(f'publication: {exc}')
            status = 'RED'
    attempts = tuple(client.attempts) if client is not None else ()
    costs = client.cost_breakdown() if client is not None else result.cost_breakdown
    if client is not None:
        try:
            client.close()
        except (Exception, KeyboardInterrupt) as exc:
            cancelled |= isinstance(exc, (RunCancelled, KeyboardInterrupt))
            errors.append(f'model close: {type(exc).__name__}: {exc}')
            status = 'RED'
    result = replace(result, status=status, candidate=candidate, checked_sha=checked_sha,
                     publication=publication, files=tuple(files.values()), selected_files=selected,
                     issues=tuple(issues), unfinished_files=tuple(sorted(unfinished)), quality=loop,
                     attempts=attempts, cost_breakdown=costs, errors=tuple(errors), cancelled=cancelled,
                     message='; '.join(errors))
    return finalize(result, hooks, publisher)
