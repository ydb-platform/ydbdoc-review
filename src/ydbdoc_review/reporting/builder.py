"""Markdown reports for source and translation PR comments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ydbdoc_review.config.loader import Config
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.pipeline.types import PRTranslationResult, PairRunResult
from ydbdoc_review.reporting.locations import (
    ReportLinkContext,
    consolidate_heuristic_warnings,
    filter_critic_for_report,
    format_location_label,
    manual_action_segment_ids,
)
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.schemas import CriticIssueOut

_VERSION = "v0.2.0"


@dataclass(frozen=True)
class ReportMeta:
    """Header metadata for a posted report."""

    mode: str  # doc_translate | doc_verify
    report_number: int
    elapsed_s: float
    timestamp: datetime | None = None

    @property
    def ts_label(self) -> str:
        ts = self.timestamp or datetime.now(timezone.utc)
        return ts.strftime("%Y-%m-%d %H:%M UTC")


def _format_duration(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs}s" if mins else f"{secs}s"


def _verdict_emoji(verdict: str) -> str:
    if verdict == "ok":
        return "🟢"
    if verdict == "warnings":
        return "🟡"
    return "🔴"


def _count_verdicts(result: PRTranslationResult) -> tuple[int, int, int]:
    ok = warn = blocked = 0
    for run in result.pair_results:
        if run.skipped or run.deleted or run.error or run.file_result is None:
            continue
        v = run.file_result.verdict
        if v == "ok":
            ok += 1
        elif v == "warnings":
            warn += 1
        else:
            blocked += 1
    return ok, warn, blocked


def _merge_recommendation(result: PRTranslationResult) -> tuple[str, str]:
    """Return (emoji, short Russian label) for merge readiness."""
    ok, warn, blocked = _count_verdicts(result)
    if blocked:
        return "🔴", "не мержить — есть блокирующие проблемы"
    if warn:
        return "🟡", "требует правок перед merge"
    if ok:
        return "🟢", "можно мержить"
    return "⚪", "нет обработанных файлов"


def _is_new_file(run: PairRunResult) -> bool:
    summary = run.plan.summary.lower()
    return "missing" in summary or "generate from" in summary


def _file_translation_counts(result: PRTranslationResult) -> tuple[int, int, int]:
    """Return (total translated, new, updated)."""
    new = updated = 0
    for run in result.pair_results:
        if run.skipped or run.deleted or run.error or run.file_result is None:
            continue
        if _is_new_file(run):
            new += 1
        else:
            updated += 1
    return new + updated, new, updated


def _location_label(
    issue: CriticIssueOut,
    segment_locations: dict[str, str],
) -> str:
    if issue.segment_id and issue.segment_id in segment_locations:
        loc = segment_locations[issue.segment_id]
        return f"{loc} (`{issue.segment_id}`)"
    if issue.segment_id:
        return f"сегмент `{issue.segment_id}`"
    return "файл целиком"


def _remaining_critic_issues(fr) -> list[CriticIssueOut]:
    """Issues the reviewer still needs to look at (not auto-applied)."""
    remaining: list[CriticIssueOut] = []
    seen: set[tuple[str | None, str, str]] = set()
    if fr.critic_unresolved:
        for issue in fr.critic_unresolved.issues:
            key = (issue.segment_id, issue.category, issue.comment)
            if key not in seen:
                seen.add(key)
                remaining.append(issue)
    for issue in fr.critic_skipped:
        key = (issue.segment_id, issue.category, issue.comment)
        if key not in seen:
            seen.add(key)
            remaining.append(issue)
    return remaining


def _format_reviewer_item(
    *,
    index: int,
    location: str,
    problem: str,
    suggestion: str | None = None,
) -> str:
    lines = [f"{index}. **{location}** — {problem}"]
    if suggestion:
        preview = suggestion.replace("\n", " ")
        if len(preview) > 240:
            preview = preview[:237] + "…"
        lines.append(f"   - 💡 Совет: {preview}")
    return "\n".join(lines)


def _format_critic_item(
    issue: CriticIssueOut,
    segment_locations: dict[str, str],
    *,
    index: int,
    file_path: str,
    segment_lines: dict[str, tuple[int, int]],
    link: ReportLinkContext | None,
) -> str:
    path_label = None
    if issue.segment_id and issue.segment_id in segment_locations:
        path_label = segment_locations[issue.segment_id]
    line_range = (
        segment_lines.get(issue.segment_id) if issue.segment_id else None
    )
    if path_label or issue.segment_id:
        location = format_location_label(
            file_path=file_path,
            segment_id=issue.segment_id,
            path_label=path_label,
            line_range=line_range,
            link=link,
        )
    else:
        location = _location_label(issue, segment_locations)
    category = issue.category.replace("_", " ")
    problem = f"({category}) {issue.comment}"
    return _format_reviewer_item(
        index=index,
        location=location,
        problem=problem,
        suggestion=issue.suggested_text,
    )


def _file_has_open_issues(run: PairRunResult) -> bool:
    fr = run.file_result
    if fr is None:
        return False
    if _remaining_critic_issues(fr):
        return True
    if fr.manual_actions:
        return True
    return bool(fr.heuristic_warnings)


def _file_reviewer_section(
    run: PairRunResult,
    *,
    config: Config,
    item_index: int,
    link: ReportLinkContext | None,
) -> tuple[str, int]:
    """Build markdown for one file's open issues; return (text, next item index)."""
    fr = run.file_result
    if fr is None or run.skipped or run.deleted or run.error:
        return "", item_index

    manual_actions = fr.manual_actions
    manual_ids = manual_action_segment_ids(manual_actions)
    critic_items = filter_critic_for_report(
        _remaining_critic_issues(fr), manual_ids
    )
    raw_heuristics = (
        fr.heuristic_warnings if config.reporting.include_heuristics else []
    )
    manual_ranges = [
        fr.segment_lines[mid]
        for mid in manual_ids
        if mid in fr.segment_lines
    ]
    heuristics = consolidate_heuristic_warnings(
        raw_heuristics,
        manual_ids=manual_ids,
        manual_line_ranges=manual_ranges,
    )

    if not critic_items and not heuristics and not manual_actions:
        if fr.verdict == "ok":
            return f"### 🟢 `{run.plan.target_path}`\n\nЗамечаний нет.\n\n", item_index
        return "", item_index

    file_path = run.plan.target_path
    out = f"### {_verdict_emoji(fr.verdict)} `{file_path}`\n\n"
    for action in manual_actions:
        line_range = fr.segment_lines.get(action.segment_id)
        location = format_location_label(
            file_path=file_path,
            segment_id=action.segment_id,
            path_label=action.location,
            line_range=line_range,
            link=link,
        )
        out += _format_reviewer_item(
            index=item_index,
            location=location,
            problem=action.message,
        ) + "\n\n"
        item_index += 1
    for issue in critic_items:
        out += (
            _format_critic_item(
                issue,
                fr.segment_locations,
                index=item_index,
                file_path=file_path,
                segment_lines=fr.segment_lines,
                link=link,
            )
            + "\n\n"
        )
        item_index += 1
    for warning in heuristics:
        out += _format_reviewer_item(
            index=item_index,
            location="эвристика (файл)",
            problem=warning,
        ) + "\n\n"
        item_index += 1
    return out, item_index


def build_commit_message(
    source_pr: int,
    result: PRTranslationResult,
    *,
    config: Config,
    verify: bool = False,
) -> str:
    """Git commit message for translation or verify fix commit."""
    if verify:
        fixed = sum(
            len(r.file_result.critic_applied)
            for r in result.pair_results
            if r.file_result
        )
        critic_model = config.llm.models.critic.primary
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"Apply critic fixes from doc_verify run on {ts}\n\n"
            f"Critic: {critic_model}\n"
            f"Fixed segments: {fixed}\n"
            f"ydbdoc-review {_VERSION}"
        )

    translated = [
        r for r in result.pair_results
        if r.file_result and not r.skipped and not r.deleted
    ]
    paths = [r.plan.target_path for r in translated if r.target_text is not None]
    lines = [
        f"Auto-translate docs from PR #{source_pr}",
        "",
        f"Translated {len(paths)} files:",
    ]
    lines.extend(f"- {p}" for p in paths[:50])
    if len(paths) > 50:
        lines.append(f"- … and {len(paths) - 50} more")
    lines.extend(
        [
            "",
            f"Translator: {config.llm.models.translate.primary}",
            f"Critic: {config.llm.models.critic.primary}",
            f"ydbdoc-review {_VERSION}",
        ]
    )
    return "\n".join(lines)


def build_translation_pr_body(source_pr: int, source_repo: str) -> str:
    return (
        f"Auto-generated translation for [{source_repo}#{source_pr}]"
        f"(https://github.com/{source_repo}/pull/{source_pr}).\n\n"
        f"Branch: `ydbdoc-review/pr-{source_pr}`\n"
    )


def build_source_pr_comment(
    result: PRTranslationResult,
    *,
    translation_pr_number: int | None,
    meta: ReportMeta,
    config: Config,
    usage: UsageTracker | None = None,
) -> str:
    """Short summary comment for the source PR."""
    rec_emoji, rec_label = _merge_recommendation(result)
    total, new_count, updated_count = _file_translation_counts(result)

    if total:
        if new_count and updated_count:
            files_label = f"{total} ({new_count} новых, {updated_count} обновлено)"
        elif new_count:
            files_label = f"{total} ({new_count} новых)"
        elif updated_count:
            files_label = f"{total} ({updated_count} обновлено)"
        else:
            files_label = str(total)
    else:
        files_label = "0"

    tr_line = f"#{translation_pr_number}" if translation_pr_number else "—"

    body = (
        "🤖 **ydbdoc-review** — перевод готов\n\n"
        f"**Рекомендация:** {rec_emoji} {rec_label}\n\n"
        "| | |\n"
        "|---|---|\n"
        f"| Translation PR | {tr_line} |\n"
        f"| Файлов | {files_label} |\n"
        f"| Время | {_format_duration(meta.elapsed_s)} |\n\n"
    )
    if translation_pr_number:
        body += (
            f"Список оставшихся проблем — в комментарии к translation PR #{translation_pr_number}.\n"
        )
    return body


def build_full_report(
    result: PRTranslationResult,
    *,
    meta: ReportMeta,
    config: Config,
    usage: UsageTracker | None = None,
    glossary: Glossary | None = None,
    link: ReportLinkContext | None = None,
) -> str:
    """Reviewer-focused QA report: open problems per file with location and advice."""
    del usage, glossary  # stats/glossary omitted from reviewer report
    rec_emoji, rec_label = _merge_recommendation(result)

    header = (
        f"🤖 **ydbdoc-review** — отчёт #{meta.report_number} "
        f"({meta.mode}, {meta.ts_label})\n\n"
        f"## Рекомендация: {rec_emoji} {rec_label}\n\n"
    )

    file_runs = [
        r for r in result.pair_results
        if r.file_result and not r.skipped and not r.deleted and not r.error
    ]
    problem_runs = [r for r in file_runs if _file_has_open_issues(r)]
    ok_runs = [r for r in file_runs if not _file_has_open_issues(r)]

    if not file_runs:
        errors = [r for r in result.pair_results if r.error]
        if errors:
            body = header + "## Ошибки pipeline\n\n"
            for run in errors:
                body += f"- `{run.plan.target_path}`: {run.error}\n"
            body += f"\n---\n\nGenerated by ydbdoc-review {_VERSION}\n"
            return body
        return header + "Нет обработанных файлов.\n"

    body = header
    if not problem_runs:
        body += "По всем файлам открытых замечаний нет.\n\n"
        for run in ok_runs:
            body += f"- 🟢 `{run.plan.target_path}`\n"
        body += f"\n---\n\nGenerated by ydbdoc-review {_VERSION}\n"
        return body

    body += "## Что исправить\n\n"
    item_index = 1
    for run in problem_runs:
        section, item_index = _file_reviewer_section(
            run, config=config, item_index=item_index, link=link
        )
        body += section

    errors = [r for r in result.pair_results if r.error]
    if errors:
        body += "## Ошибки pipeline\n\n"
        for run in errors:
            body += f"- `{run.plan.target_path}`: {run.error}\n"
        body += "\n"

    if ok_runs:
        body += "## Без замечаний\n\n"
        for run in ok_runs:
            body += f"- 🟢 `{run.plan.target_path}`\n"
        body += "\n"

    body += f"---\n\nGenerated by ydbdoc-review {_VERSION}\n"
    return body
