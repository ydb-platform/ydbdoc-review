"""Markdown reports for source and translation PR comments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ydbdoc_review.config.loader import Config
from ydbdoc_review.pipeline.types import PRTranslationResult, PairRunResult
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


def _aggregate_usage(result: PRTranslationResult) -> dict[str, float | int]:
    inp = out = 0
    cost = 0.0
    for run in result.pair_results:
        fr = run.file_result
        if fr is None:
            continue
        inp += fr.input_tokens
        out += fr.output_tokens
        cost += fr.estimated_cost_usd
    return {"input_tokens": inp, "output_tokens": out, "estimated_cost_usd": cost}


def _aggregate_models(result: PRTranslationResult) -> list[str]:
    models: set[str] = set()
    for run in result.pair_results:
        fr = run.file_result
        if fr:
            models.update(fr.models_used)
    return sorted(models)


def _written_paths(result: PRTranslationResult) -> list[str]:
    paths: list[str] = []
    for run in result.pair_results:
        if run.error or run.skipped:
            continue
        if run.deleted:
            paths.append(run.plan.target_path)
            continue
        if run.target_text is not None:
            paths.append(run.plan.target_path)
    return paths


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
    new_count = sum(
        1 for r in translated if r.plan.action == "translate_to_en" and r.plan.summary
    )
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
) -> str:
    """Short summary comment for the source PR."""
    ok, warn, blocked = _count_verdicts(result)
    translated = sum(
        1
        for r in result.pair_results
        if r.file_result and not r.skipped and not r.deleted
    )
    usage = _aggregate_usage(result)
    status_parts: list[str] = []
    if ok:
        status_parts.append(f"{ok} OK")
    if warn:
        status_parts.append(f"{warn} требует ревью")
    if blocked:
        status_parts.append(f"{blocked} blocked")
    status = ", ".join(status_parts) if status_parts else "нет файлов"

    tr_line = f"#{translation_pr_number}" if translation_pr_number else "—"
    cost_line = ""
    if config.reporting.include_cost and usage["estimated_cost_usd"] > 0:
        cost_line = f"| Стоимость | ~${usage['estimated_cost_usd']:.2f} |\n"

    mins, secs = divmod(int(meta.elapsed_s), 60)
    time_label = f"{mins}m {secs}s" if mins else f"{secs}s"

    body = (
        "🤖 **ydbdoc-review** — перевод готов\n\n"
        "| | |\n"
        "|---|---|\n"
        f"| Translation PR | {tr_line} |\n"
        f"| Файлов переведено | {translated} |\n"
        f"| Статус QA | {status} |\n"
        f"| Время | {time_label} |\n"
        f"{cost_line}\n"
    )
    if translation_pr_number:
        body += f"👉 Полный отчёт в translation PR #{translation_pr_number}.\n"
    return body


def _issue_lines(issues: list[CriticIssueOut], *, applied: bool) -> list[str]:
    lines: list[str] = []
    for issue in issues:
        flag = "🟢 auto-applied" if applied else "🔴 unresolved"
        lines.append(
            f"- `{issue.segment_id}` ({issue.category})\n"
            f"  - {issue.comment}\n"
            f"  - {flag}"
        )
    return lines


def build_full_report(
    result: PRTranslationResult,
    *,
    meta: ReportMeta,
    config: Config,
) -> str:
    """Full QA report for the translation PR."""
    ok, warn, blocked = _count_verdicts(result)
    usage = _aggregate_usage(result)
    models = _aggregate_models(result)

    header = (
        f"🤖 **ydbdoc-review** — отчёт #{meta.report_number} "
        f"({meta.mode}, {meta.ts_label})\n\n"
    )
    verdict_line = f"## Вердикт: {_verdict_emoji('ok') if ok and not warn and not blocked else '🟡'} "
    parts = [ok, warn, blocked]
    labels = ["OK", "требует ревью", "blocked"]
    verdict_bits = [f"{n} {lbl}" for n, lbl in zip(parts, labels) if n]
    verdict_line += ", ".join(verdict_bits) if verdict_bits else "нет обработанных файлов"
    verdict_line += "\n\n"

    table = "| Файл | Статус | Critic issues | Heuristic warnings |\n"
    table += "|---|---|---|---|\n"
    for run in result.pair_results:
        if run.skipped:
            continue
        path = run.plan.target_path
        if run.error:
            table += f"| `{path}` | 🔴 error | — | — |\n"
            continue
        if run.deleted:
            table += f"| `{path}` | 🗑 deleted | — | — |\n"
            continue
        fr = run.file_result
        if fr is None:
            continue
        applied = len(fr.critic_applied)
        unresolved = len(fr.critic_unresolved.issues) if fr.critic_unresolved else 0
        hw = len(fr.heuristic_warnings)
        table += (
            f"| `{path}` | {_verdict_emoji(fr.verdict)} {fr.verdict} | "
            f"{applied} fixed, {unresolved} unresolved | {hw} |\n"
        )

    applied_total = sum(
        len(r.file_result.critic_applied)
        for r in result.pair_results
        if r.file_result
    )
    unresolved_total = sum(
        len(r.file_result.critic_unresolved.issues)
        for r in result.pair_results
        if r.file_result and r.file_result.critic_unresolved
    )
    hw_total = sum(
        len(r.file_result.heuristic_warnings)
        for r in result.pair_results
        if r.file_result
    )
    failed = result.failed_count

    mins, secs = divmod(int(meta.elapsed_s), 60)
    time_label = f"{mins}m {secs}s" if mins else f"{secs}s"

    summary = "## Сводка\n"
    summary += f"- Файлов обработано: {result.translated_count}\n"
    summary += f"- Critic fixes auto-applied: {applied_total}\n"
    summary += f"- Critic fixes unresolved: {unresolved_total}\n"
    summary += f"- Heuristic warnings: {hw_total}\n"
    summary += f"- Ошибок API: {failed}\n"
    summary += f"- Время: {time_label}\n"
    if config.reporting.include_token_usage:
        summary += (
            f"- Tokens: {usage['input_tokens']}/{usage['output_tokens']}\n"
        )
    if config.reporting.include_cost:
        summary += f"- Cost: ~${usage['estimated_cost_usd']:.2f}\n"
    if models:
        summary += f"- Models: {', '.join(models)}\n"
    summary += f"- Prompt version: {config.prompts.version}\n\n"

    details = "## Детали по файлам\n\n"
    for run in result.pair_results:
        fr = run.file_result
        if fr is None or run.skipped or run.deleted or run.error:
            continue
        details += f"### {_verdict_emoji(fr.verdict)} `{run.plan.target_path}`\n\n"
        if fr.critic_applied:
            details += "**Critic issues (auto-applied)**\n"
            details += "\n".join(_issue_lines(fr.critic_applied, applied=True))
            details += "\n\n"
        if fr.critic_unresolved and fr.critic_unresolved.issues:
            details += "**Critic issues (unresolved)**\n"
            details += "\n".join(
                _issue_lines(fr.critic_unresolved.issues, applied=False)
            )
            details += "\n\n"
        if fr.heuristic_warnings and config.reporting.include_heuristics:
            details += "**Heuristic warnings**\n"
            for w in fr.heuristic_warnings:
                details += f"- {w}\n"
            details += "\n"

    footer = f"\n---\n\nGenerated by ydbdoc-review {_VERSION}\n"
    return header + verdict_line + table + "\n" + summary + details + footer
