"""Two small PR reports from one result; no model transcripts or legacy renderer.

Rendering is offline. Sending requires an explicitly authorized production run.
The caller owns GitHub credentials and RunStore lifetime (including create_store).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate
from ydbdoc_review.runner import RunResult


@dataclass(frozen=True)
class Comment:
    pr: str  # owner/repository/number, not the push repository of a fork
    body: str


def _identity(pr: str) -> str:
    if not re.fullmatch(r'[\w.-]+/[\w.-]+/[1-9][0-9]*', pr):
        raise ValueError('Expected PR identity owner/repository/number')
    return pr


def _url(pr: str) -> str:
    repository, number = _identity(pr).rsplit('/', 1)
    return f'https://github.com/{repository}/pull/{number}'


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(?:bearer\s+|(?:token|api[_-]?key|password)\s*[=:]\s*)[^\s,;]+',
                  '[REDACTED]', text)
    text = re.sub(r'https?://[^/\s@]+@', 'https://[REDACTED]@', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)',
                  '[REDACTED]', text)
    return text


def _safe(text: str, secrets: tuple[str, ...]) -> str:
    text = _redact(text, secrets)
    # HTTP exception bodies may contain echoed prompts/responses or credentials.
    text = re.sub(r'(HTTP [45][0-9]{2})[^\n]*', r'\1', text)
    return text.replace('<', '&lt;').replace('>', '&gt;')


def _costs(result: RunResult) -> str:
    labels = (('translation', 'Перевод'), ('critic', 'Критик'),
              ('repair', 'Исправления'), ('total', 'Итого'))
    return '\n'.join(f'- {label}: ' + (f'{value:f} ₽' if value is not None else 'неизвестно (RUB)')
                     for key, label in labels for value in (result.cost_breakdown.get(key),))


def _location(location, text, repository, sha, path, label, clean, secrets):
    if location is None or text is None:
        return f'{label}: место не установлено.'
    try:
        location.validate(text)
    except ValueError:
        return f'{label}: место не установлено; цитата не подтверждена снимком.'
    lines = str(location.start) if location.start == location.end else f'{location.start}-{location.end}'
    anchor = f'L{location.start}' + (f'-L{location.end}' if location.end != location.start else '')
    link = f'https://github.com/{repository}/blob/{sha}/{quote(path, safe="/")}#{anchor}'
    # Validate original text first, redact the whole excerpt (also multiline
    # secrets), then let a code fence preserve Markdown and HTML entities.
    excerpt = _redact(location.quote, secrets)
    suffix = '\n\n… [цитата сокращена]' if len(excerpt) > 500 else ''
    excerpt = excerpt[:500]
    fence = '`' * max(3, 1 + max(map(len, re.findall(r'`+', excerpt)), default=0))
    block = fence + '\n' + excerpt + ('' if excerpt.endswith('\n') else '\n') + fence
    return f'{label}: [{clean(path)}:{lines}]({link})\n\n' + '\n'.join(
        '> ' + line for line in block.split('\n')) + suffix


def render_reports(result: RunResult, *, current_pr: str, source_pr: str | None = None,
                   secrets: tuple[str, ...] = ()) -> tuple[Comment, ...]:
    """Current PR is mandatory even when ACL/metadata preflight produced no snapshot.

    Continue's snapshot retains the original source PR. Explicit source_pr supports
    a separately associated verify; otherwise ordinary verify has one identity.
    """
    current_pr = _identity(current_pr)
    snapshot = result.snapshot
    source = _identity(source_pr) if source_pr else (
        f'{snapshot.owner}/{snapshot.repo}/{snapshot.pr_number}' if snapshot else current_pr)
    publication = result.publication
    target = (f'{publication.repository}/{publication.pr_number}'
              if publication and publication.pr_number else
              current_pr if result.mode != 'doc_translate' else None)
    def clean(text):
        return _safe(str(text), secrets)
    if result.status == 'NO_WORK':
        verdict = 'Перевод не требуется'
    elif result.status == 'GREEN' and result.checked_sha and result.checked_sha == result.candidate_sha:
        verdict = 'GREEN — перевод прошёл проверки и готов к слиянию.'
    else:
        verdict = 'RED — мержить нельзя, нужны исправления.'
    common = [verdict]
    for text in dict.fromkeys(filter(None, (result.message, *result.errors))):
        if text != verdict:
            common.append(clean(text))
    if result.cancelled:
        common.append('Запуск прерван; сохранена доступная часть результата.')
    paths = {f.path for f in result.selected_files} | {f.path for f in result.files}
    if result.plan:
        paths.update(op.target_new_path or op.target_old_path for op in result.plan.operations)
    finished = sorted(paths - set(result.unfinished_files) - {None})
    if finished:
        common.append('Обработанные файлы: ' + ', '.join(clean(p) for p in finished))
    if result.unfinished_files:
        common.append('Незавершённые файлы: ' + ', '.join(map(clean, result.unfinished_files)))
    common.append(f'Результат: [PR]({_url(target)}).' if target else 'Переводной PR не создан.')
    costs = _costs(result)
    brief = '\n\n'.join([*common, costs])
    detail = [*common, f'SHA последнего проверенного коммита: {result.checked_sha or "отсутствует"}.']
    if result.candidate_sha != result.checked_sha and result.candidate_sha:
        detail.append(f'Кандидат: {result.candidate_sha} — этот SHA не проверен.')
    published = bool(result.result_sha and result.result_sha == result.candidate_sha)
    detail.append(f'Опубликованный SHA: {result.result_sha}.' if published else
                  'Опубликованный SHA не подтверждён; локальные координаты не являются опубликованными.')
    checks = result.quality.rounds[-1].checks if result.quality and result.quality.rounds else ()
    if checks:
        detail.append('Критик: ' + ('ошибок не обнаружено.' if all(check.ok for check in checks)
                                   else 'есть замечания или проверка не завершена.'))
    else:
        detail.append('Критик: завершённая проверка не зафиксирована (возможен запуск без модели).')
    for issue in result.issues:
        detail.append(f'{clean(issue.path)} — {issue.severity}: {clean(issue.problem)}\n\n'
                      f'Исправить вручную: {clean(issue.expected_fix)}')
        data = result.candidate.read(issue.path) if result.candidate else None
        try:
            text = data.decode('utf-8') if data is not None else None
        except UnicodeDecodeError:
            text = None
        if published:
            detail.append(_location(issue.target, text, publication.repository, result.result_sha,
                                    issue.path, 'Перевод / место исправления', clean, secrets))
        else:
            detail.append('Перевод: опубликованное место исправления не установлено.')
        if data is None and result.candidate:
            detail.append('Файл отсутствует в кандидате; строки перевода отсутствуют.')
        elif text is None:
            detail.append('Текст файла недоступен; строки перевода не установлены.')
        if issue.source:
            source_path = next((op.new_path for op in result.plan.operations
                                if op.target_new_path == issue.path), None) if result.plan else None
            source_text = result.plan.files.get(source_path) if result.plan and source_path else None
            if source_path is None and snapshot and result.candidate:
                selected = next((f for f in result.selected_files if f.path == issue.path), None)
                if selected and issue.path.startswith(f'ydb/docs/{selected.target_lang}/'):
                    language = 'ru' if selected.target_lang == 'en' else 'en'
                    proposed = f'ydb/docs/{language}/' + issue.path.split('/', 3)[3]
                    original = Candidate.open(result.candidate.repo, snapshot.source_sha)
                    if original.text(proposed) == selected.source:
                        source_path, source_text = proposed, selected.source
            if source_path and snapshot:
                detail.append(_location(issue.source, source_text, snapshot.source_repo,
                                        snapshot.source_sha, source_path, 'Исходник', clean, secrets))
            else:
                detail.append('Исходник: путь и строки не установлены; цитата не подтверждена снимком.')
    detail.append(costs)
    detailed = '\n\n'.join(detail)
    if target is None:
        return (Comment(source, brief),)
    if source == target:
        return (Comment(target, detailed),)
    return (Comment(source, brief), Comment(target, detailed))


def create_reporter(github: GitHubClient, *, current_pr: str, source_pr: str | None = None,
                    authorized: bool = False, secrets: tuple[str, ...] = ()):
    """A RunHooks.report callback. No HTTP on construction; never retries comments.

    authorized must be set by the production entrypoint after run authorization.
    Tests replace requests' HTTP boundary, not this adapter or GitHubClient.
    """
    _identity(current_pr)
    if source_pr is not None:
        _identity(source_pr)

    def report(result: RunResult):
        if not authorized:
            raise PermissionError('GitHub report publication requires an authorized production run')
        for comment in render_reports(result, current_pr=current_pr, source_pr=source_pr, secrets=secrets):
            owner, repository, number = comment.pr.split('/')
            github.post_issue_comment(owner, repository, int(number), comment.body)
    return report


def report_hooks(adapter, github: GitHubClient, *, current_pr: str,
                 authorized: bool = False, cancelled=None, secrets: tuple[str, ...] = ()):
    """Wire a real RunStore (backed by create_store()) to runner save/report hooks.

    Continue owns its RunStore internally: pass RunHooks(report=create_reporter(...))
    there, allowing its original-source snapshot to select the source destination.
    """
    return adapter.hooks(report=create_reporter(github, current_pr=current_pr,
                                               source_pr=adapter.source_pr,
                                               authorized=authorized, secrets=secrets),
                         cancelled=cancelled)
