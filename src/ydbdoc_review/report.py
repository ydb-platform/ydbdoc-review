"""Two small PR reports from one result; no model transcripts or legacy renderer.

Rendering is offline. Sending requires an explicitly authorized reporting callback.
The caller owns GitHub credentials and RunStore lifetime (including create_store).
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, replace
from urllib.parse import quote

from ydbdoc_review.diagnostics import redact_known
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate, safe_path
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
    text = redact_known(text, secrets)
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
        safe_path(path)
        location.validate(text)
    except ValueError:
        return f'{label}: место не установлено; цитата не подтверждена снимком.'
    lines = str(location.start) if location.start == location.end else f'{location.start}-{location.end}'
    anchor = f'L{location.start}' + (f'-L{location.end}' if location.end != location.start else '')
    link = f'https://github.com/{repository}/blob/{sha}/{quote(_redact(path, secrets), safe="/")}#{anchor}'
    # Validate original text first, redact the whole excerpt (also multiline
    # secrets), then let a code fence preserve Markdown and HTML entities.
    excerpt = _redact(location.quote, secrets)
    suffix = '\n\n… [цитата сокращена]' if len(excerpt) > 500 else ''
    excerpt = excerpt[:500]
    fence = '`' * max(3, 1 + max(map(len, re.findall(r'`+', excerpt)), default=0))
    block = fence + '\n' + excerpt + ('' if excerpt.endswith('\n') else '\n') + fence
    return f'{label}: [{clean(path)}:{lines}]({link})\n\n' + '\n'.join(
        '> ' + line for line in block.split('\n')) + suffix


# Far below GitHub's 65536 character limit; costs and SHA are never sliced away.
REPORT_BODY_LIMIT = 12000


def _short(text, limit):
    return text if len(text) <= limit else text[:limit] + '… [сокращено]'


def _path_list(paths, clean):
    paths = list(paths)
    return ', '.join(_short(clean(p), 160) for p in paths[:5]) + (
        f'; ещё {len(paths) - 5}' if len(paths) > 5 else '')


def _examples(issues):
    seen = set()
    for issue in sorted(issues, key=lambda i: i.severity != 'error'):
        if issue.code not in seen:
            seen.add(issue.code)
            yield issue
            if len(seen) == 3:
                break


def _bounded(parts, costs):
    # Sections have independent small budgets, including untrusted error text.
    text = '\n\n'.join(parts) + '\n\n' + costs
    if len(text) > REPORT_BODY_LIMIT:
        # Preserve the verdict, SHA and costs even with adversarial paths/quotes.
        essentials = [p for p in parts if 'SHA' in p]
        text = _short('\n\n'.join(parts), 7000) + '\n\n' + '\n\n'.join(essentials) + '\n\n' + costs
    if len(text) > REPORT_BODY_LIMIT:
        raise ValueError('Report exceeds concise body limit')
    return text


class ReportDeliveryError(RuntimeError):
    """All channels were attempted. F11 may persist these small failure records."""
    def __init__(self, errors, *, cancelled=False):
        self.cancelled = cancelled
        self.errors = tuple(errors)
        super().__init__('; '.join(f'{channel}: {message}' for channel, message in errors))


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
        return _short(_safe(str(text), secrets), 600)
    if result.status == 'NO_WORK':
        verdict = 'Перевод не требуется'
    elif (result.status == 'GREEN' and result.checked_sha and result.checked_sha == result.candidate_sha
          and not result.errors and not result.unfinished_files and not result.cancelled
          and not any(i.severity == 'error' for i in result.issues)
          and (not result.quality or result.quality.status == 'GREEN')):
        verdict = 'GREEN — перевод прошёл проверки и готов к слиянию.'
    else:
        verdict = 'RED — мержить нельзя, нужны исправления.'
    common = [verdict]
    for text in list(dict.fromkeys(filter(None, (result.message, *result.errors))))[:3]:
        if text != verdict:
            common.append(clean(text))
    if result.cancelled:
        common.append('Запуск прерван; сохранена доступная часть результата.')
    paths = {f.path for f in result.selected_files} | {f.path for f in result.files}
    if result.plan:
        paths.update(op.target_new_path or op.target_old_path for op in result.plan.operations)
    finished = sorted(paths - set(result.unfinished_files) - {None})
    if finished:
        common.append('Обработанные файлы: ' + _path_list(finished, clean))
    if result.unfinished_files:
        common.append('Незавершённые файлы: ' + _path_list(result.unfinished_files, clean))
    common.append(f'Результат: [PR]({_url(target)}).' if target else 'Переводной PR не создан.')
    if result.issues:
        counts = Counter(issue.code for issue in result.issues)
        common.append(f'Замечаний: {len(result.issues)} по {len({i.path for i in result.issues})} путям. ' +
                      '; '.join(f'{clean(code)}: {count}' for code, count in counts.most_common(6)))
        common.append('Основные причины и действия:\n' + '\n'.join(
            f'- {clean(i.path)}: {clean(i.problem)} Исправить: {clean(i.expected_fix)}'
            for i in _examples(result.issues)))
    if result.quality and result.quality.rounds:
        last = result.quality.rounds[-1]
        if last.links and last.links.unchecked_anchors:
            common.append(f'Якоря не проверены: {len(last.links.unchecked_anchors)}. '
                          'Сначала завершите сборку, затем повторите проверку ссылок.')
    costs = _costs(result)
    brief = _bounded(common, costs)
    detail = [*(p for p in common if not p.startswith('Основные причины и действия:')), f'SHA последнего проверенного коммита: {result.checked_sha or "отсутствует"}.']
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
    for issue in _examples(result.issues):
        detail.append(f'{clean(issue.path)} — {issue.severity}: {clean(issue.problem)}\n\n'
                      f'Исправить вручную: {clean(issue.expected_fix)}')
        read_error = None
        try:
            data = result.candidate.read(issue.path) if result.candidate else None
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            data = None
            read_error = exc
        try:
            text = data.decode('utf-8') if data is not None else None
        except UnicodeDecodeError:
            text = None
        if published:
            detail.append(_location(issue.target, text, publication.repository, result.result_sha,
                                    issue.path, 'Перевод / место исправления', clean, secrets))
        else:
            detail.append('Перевод: опубликованное место исправления не установлено.')
        if read_error is not None:
            detail.append('Текст файла недоступен; строки перевода не установлены: ' + clean(read_error))
        elif data is None and result.candidate:
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
                    try:
                        original = Candidate.open(result.candidate.repo, snapshot.source_sha)
                        if original.text(proposed) == selected.source:
                            source_path, source_text = proposed, selected.source
                    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                        detail.append('Исходник недоступен; строки не установлены: ' + clean(exc))
            if source_path and snapshot:
                detail.append(_location(issue.source, source_text, snapshot.source_repo,
                                        snapshot.source_sha, source_path, 'Исходник', clean, secrets))
            else:
                detail.append('Исходник: путь и строки не установлены; цитата не подтверждена снимком.')
    detail.append(costs)
    detailed = _bounded(detail[:-1], costs)
    if target is None:
        return (Comment(source, brief),)
    if source == target:
        return (Comment(target, detailed),)
    translated_detail = _bounded([f"Это перевод [исходного PR]({_url(source)}).", *detail[:-1]], costs)
    return (Comment(source, brief), Comment(target, translated_detail))



def create_reporter(github: GitHubClient, *, current_pr: str, source_pr: str | None = None,
                    authorized: bool = False, secrets: tuple[str, ...] = (),
                    refusal_only: bool = False):
    """A RunHooks.report callback. No HTTP on construction; never retries comments.

    authorized is internal delivery authorization, including preflight refusals.
    refusal_only restricts pre-admission delivery to the triggering PR comment.
    It never grants admission to the model, document writes or PR creation.
    Tests replace requests' HTTP boundary, not this adapter or GitHubClient.
    """
    _identity(current_pr)
    if source_pr is not None:
        _identity(source_pr)

    receipts = {}
    started = False

    def progress(result):
        nonlocal started
        started = True
        deliver(result, preliminary=True)

    def deliver(result: RunResult, preliminary=False):
        if not authorized:
            raise PermissionError('GitHub report publication requires an authorized production run')
        if refusal_only:
            # Before ACL/config admission the sole capability is a refusal
            # comment in the triggering PR, never GitData or PR mutations.
            refusal = replace(result, status='RED', publication=None, snapshot=None)
            comment = render_reports(refusal, current_pr=current_pr, secrets=secrets)[-1]
            owner, repository, number = current_pr.split('/')
            github.post_issue_comment(owner, repository, int(number), comment.body)
            return
        failures = []
        interrupted = False
        def rendered():
            return render_reports(result, current_pr=current_pr, source_pr=source_pr,
                                  secrets=secrets)
        destinations = rendered()
        for index, comment in enumerate(destinations):
            owner, repository, number = comment.pr.split('/')
            try:
                if preliminary:
                    receipts[comment.pr] = github.create_report_comment(
                        owner, repository, int(number), rendered()[index].body)
                elif started:
                    if comment.pr in receipts:
                        github.update_report_comment(owner, repository, receipts[comment.pr],
                                                     rendered()[index].body)
                else:
                    github.post_issue_comment(owner, repository, int(number), rendered()[index].body)
            except (Exception, KeyboardInterrupt) as exc:
                interrupted |= isinstance(exc, KeyboardInterrupt)
                failures.append((f'comment {comment.pr}', _safe(str(exc), secrets)))
        if failures:
            raise ReportDeliveryError(failures, cancelled=interrupted)

    def reconcile_red(result):
        if not authorized or refusal_only:
            raise PermissionError('GitHub report reconciliation requires an admitted production run')
        failures = []
        interrupted = False
        comments = render_reports(replace(result, status='RED'), current_pr=current_pr,
                                  source_pr=source_pr, secrets=secrets)
        for comment in comments:
            if comment.pr not in receipts:
                continue
            owner, repository, _ = comment.pr.split('/')
            try:
                github.update_report_comment(owner, repository, receipts[comment.pr], comment.body)
            except (Exception, KeyboardInterrupt) as exc:
                interrupted |= isinstance(exc, KeyboardInterrupt)
                failures.append((f'comment reconciliation {comment.pr}', _safe(str(exc), secrets)))
        if failures:
            raise ReportDeliveryError(failures, cancelled=interrupted)

    deliver.progress = progress
    deliver.reconcile_red = reconcile_red
    return deliver


def report_hooks(adapter, github: GitHubClient, *, current_pr: str,
                 authorized: bool = False, cancelled=None, secrets: tuple[str, ...] = ()):
    """Wire a real RunStore (backed by create_store()) to runner save/report hooks.

    Continue owns its RunStore internally: pass RunHooks(report=create_reporter(...))
    there, allowing its original-source snapshot to select the source destination.
    """
    return adapter.hooks(report=create_reporter(github, current_pr=current_pr,
                                               source_pr=adapter.source_pr,
                                               authorized=authorized, secrets=secrets),
                         cancelled=cancelled, secrets=secrets)
