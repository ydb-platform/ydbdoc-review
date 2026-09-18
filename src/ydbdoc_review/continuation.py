"""Continue saved work in its existing PR; CLI routing belongs to T15.

Every call owns a NEW RunStore. Only a durable, admitted attempt consumes one of
three slots per original PR. Preflight/budget refusals do not consume a slot.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from ydbdoc_review.build import build_candidate
from ydbdoc_review.config.loader import Settings, require_actor
from ydbdoc_review.document import RequestBudget, file_result_from_dict
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate
from ydbdoc_review.model import ModelChoice, ModelClient
from ydbdoc_review.ops.continue_cmd import find_latest_continue_instruction
from ydbdoc_review.plan import PlanError, Snapshot, freeze_snapshot
from ydbdoc_review.publication import PublicationError, Publisher, freeze
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.quality_loop import QualityLoopInterrupted, SelectedFile, run_quality_loop
from ydbdoc_review.runner import RunCancelled, RunHooks, RunResult, finalize
from ydbdoc_review.store import RunStore, StorageError, YDBStore

CHANGED = ('После предыдущего запуска появились новые коммиты. Запустите doc_verify '
           'для проверки текущего перевода или doc_translate для нового перевода')


def select_files(context: dict, instruction: str) -> tuple[SelectedFile, ...]:
    """Unambiguous known names/paths plus saved errors/unfinished."""
    previous = context['result']
    known = {f['path']: f for f in context.get('known_files', previous.get('selected_files', ()))}
    aliases: dict[str, set[str]] = {}
    for path, file in known.items():
        lang = file['target_lang']
        source = path.replace(f'/docs/{lang}/', '/docs/' + ('ru' if lang == 'en' else 'en') + '/', 1)
        for alias in (path, source, path.rsplit('/', 1)[-1]):
            aliases.setdefault(alias, set()).add(path)
    file_pattern = re.compile(r'(?<![\w/.-])(?:[\w.-]+/)*[\w.-]+\.md(?![\w/.-])')
    mentions = set(file_pattern.findall(instruction))
    unknown = mentions - aliases.keys()
    if unknown:
        raise PlanError('Неизвестные имена или пути файлов: ' + ', '.join(sorted(unknown)))
    ambiguous = [name + ': ' + ', '.join(sorted(aliases[name]))
                 for name in sorted(mentions) if len(aliases[name]) > 1]
    if ambiguous:
        raise PlanError('Неоднозначный выбор файла. Уточните путь: ' + '; '.join(ambiguous))
    resolved = {name: next(iter(aliases[name])) for name in mentions}
    explicit = set(resolved.values())
    findings = {}
    for data in previous['issues']:
        if data['severity'] != 'error' or data['path'] not in known:
            continue
        issue = Issue(**{**data, 'source': Location(**data['source']) if data['source'] else None,
                         'target': Location(**data['target']) if data['target'] else None})
        findings.setdefault(issue.path, []).append(issue)
    unfinished = set(previous['unfinished_files'])
    if unfinished - known.keys():
        raise StorageError('Unfinished files have no saved source context')
    paths = set(findings) | unfinished | explicit
    if not paths:
        raise PlanError('Нет проблемных файлов. Укажите в /ydbdoc continue однозначное имя или путь '
                        'известного файла и требуемое исправление.')
    selected = []
    for path in sorted(paths):
        file = known[path]
        lines = []
        for line in instruction.splitlines():
            line_paths = {resolved[p] for p in file_pattern.findall(line)}
            if not line_paths or path in line_paths:
                lines.append(line)
        relevant = '\n'.join(lines)
        requested = list(findings.get(path, ()))
        if path in unfinished:
            requested.append(Issue(path, 'Предыдущая проверка файла не завершена',
                                   'Завершить исправление и проверку', 'continue_unfinished'))
        if relevant:
            requested.append(Issue(path, 'Инструкция техписа: ' + relevant,
                                   relevant, 'continue_instruction'))
        selected.append(SelectedFile(path, file['source'], file['target_lang'], relevant,
                                     tuple(tuple(pair) for pair in file['glossary']),
                                     initial=(file_result_from_dict(file['initial'])
                                              if file.get('initial') else None),
                                     requested_findings=tuple(requested)))
    return tuple(selected)


def run_continue(*, repo: str | Path, github: GitHubClient, owner: str, repository: str,
                 pr_number: int, actor: str, settings: Settings, publisher: Publisher,
                 store: YDBStore, critic_choice: ModelChoice, repair_choice: ModelChoice,
                 budget: RequestBudget, hooks: RunHooks | None = None,
                 model_factory: Callable[[RunStore], ModelClient] | None = None,
                 model_options: Mapping | None = None, build=build_candidate,
                 source_snapshot: Snapshot | None = None,
                 target_snapshot: Snapshot | None = None) -> RunResult:
    """Real component entry: lookup → SHA guards → admission → loop → same PR/save.

    Caller fetches commit objects into repo and may pass their frozen snapshots.
    Without snapshots, direct callers freeze each PR once here.
    Default model factory records through
    the new adapter; optional factory receives that adapter, never the old run_id.
    hooks add reporting/observability to mandatory persistence, not replace it.
    """
    hooks = hooks or RunHooks()
    adapter = RunStore(store, mode='doc_continue', source_pr=f'{owner}/{repository}/{pr_number}')
    result = RunResult(mode='doc_continue')
    client = candidate = loop = checked_sha = expected_head = publication_snapshot = None
    selected = ()
    known = ()
    errors = []
    unfinished = set()
    cancelled = False
    status = 'RED'

    def save(value):
        adapter.save(value, known_files=known)
        if hooks.save:
            hooks.save(value)

    final_hooks = replace(hooks, save=save)

    def check_cancel():
        if hooks.cancelled and hooks.cancelled():
            raise RunCancelled('Run cancelled; available results retained')

    def checked_build(tree):
        check_cancel()
        return build(tree)

    def retain(tree):
        try:
            adapter.candidate_progress(tree)
            if hooks.candidate_progress:
                hooks.candidate_progress(tree)
        except Exception as exc:
            errors.append(f'candidate storage: {exc}')

    def commit(previous, updates):
        nonlocal candidate
        candidate = freeze(previous, updates)
        retain(candidate)
        return candidate

    try:
        require_actor(settings, actor)
        comments = [comment for comment in github.iter_issue_comments(owner, repository, pr_number)
                    if re.match(r'^(?:@\S+\s+)?/ydbdoc continue(?:\s|$)',
                                str(comment.get('body') or '').strip(), re.IGNORECASE)]
        instruction = find_latest_continue_instruction(
            comments, allowed_actors=settings.allowed_actors, before=store.clock())
        if not instruction:
            raise PlanError('Добавьте комментарий /ydbdoc continue <инструкция> от разрешённого автора.')
        context = store.latest_context(adapter.source_pr)
        adapter.source_pr = context['source_pr']
        source_owner, source_repo, source_number = adapter.source_pr.split('/')
        source = source_snapshot or freeze_snapshot(github, source_owner, source_repo, int(source_number))
        receipt = context['result']['publication']
        target_owner, target_repo = receipt['repository'].split('/')
        target = target_snapshot or freeze_snapshot(github, target_owner, target_repo, receipt['pr_number'])
        result = replace(result, snapshot=source)
        if source.source_sha != context['source_sha'] or target.head_sha != context['result_sha']:
            raise PlanError(CHANGED)
        if target.merged:
            raise PlanError('Продолжение требует открытый существующий PR.')
        if (publisher.pr_number != receipt['pr_number']
                or publisher.repository not in {receipt['repository'], target.source_repo}
                or publisher.pr_repository not in {None, receipt['repository']}
                or publisher.branch != target.publication_base
                or publisher.branch != receipt['branch']):
            raise PublicationError('doc_continue publisher must address the saved existing PR and head branch')
        remote_url = publisher.remote_url
        if (publisher.repository == receipt['repository'] and target.source_repo != receipt['repository']
                and remote_url in {f"https://github.com/{receipt['repository']}",
                                   f"https://github.com/{receipt['repository']}.git"}):
            remote_url = f'https://github.com/{target.source_repo}.git'
        publisher = replace(publisher, repository=target.source_repo, remote_url=remote_url,
                            pr_repository=receipt['repository'])
        publication_snapshot = target
        expected_head = publisher.preflight(target)
        if expected_head != context['result_sha']:
            raise PlanError(CHANGED)
        tree = Candidate.open(repo, expected_head)
        for path, data in context['final_files'].items():
            if tree.read(path) != data:
                raise StorageError(f'Saved bytes differ from exact result SHA: {path}')
        selected = select_files(context, instruction)
        known = context.get('known_files', context['result'].get('selected_files', ()))
        check_cancel()
        adapter.admit(settings.daily_budget_rub)
        adapter.continuation_count = store.claim_continuation(context['original_pr'], adapter.run_id)
        client = (model_factory(adapter) if model_factory else
                  adapter.model_factory(**dict(model_options or {})))
        candidate = tree
        unfinished.update(file.path for file in selected)
        retain(candidate)
        loop = run_quality_loop(candidate, selected, client=client, critic_choice=critic_choice,
                                repair_choice=repair_choice, budget=budget, freeze=commit,
                                build=checked_build)
        candidate = loop.candidate
        checked_sha = loop.checked_sha
        unfinished = set(loop.unfinished_files)
        if checked_sha != candidate.sha:
            unfinished.update(file.path for file in selected)
        status = loop.status
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, QualityLoopInterrupted):
            loop = exc.result
            checked_sha = loop.checked_sha
            unfinished.update(loop.unfinished_files)
        cancelled = isinstance(exc, (RunCancelled, KeyboardInterrupt))
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
            publication = publisher.publish(candidate, publication_snapshot, expected_head=expected_head,
                                            status=status, checked_sha=checked_sha,
                                            report_result=replace(result, status=status, candidate=candidate,
                                                checked_sha=checked_sha, selected_files=selected, quality=loop,
                                                issues=loop.issues if loop else (),
                                                unfinished_files=tuple(sorted(unfinished)), errors=tuple(errors),
                                                cost_breakdown=costs))
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
    return finalize(result, final_hooks, publisher)
