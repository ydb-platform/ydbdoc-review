"""Markdown reports for source and translation PR comments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ydbdoc_review.config.loader import Config
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.pipeline.types import PRTranslationResult, PairRunResult
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.schemas import CriticIssueOut

_VERSION = "v0.2.0"
_GLOSSARY_PREVIEW_LIMIT = 15


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


def _overall_verdict_emoji(ok: int, warn: int, blocked: int) -> str:
    if blocked:
        return "🔴"
    if warn:
        return "🟡"
    if ok:
        return "🟢"
    return "⚪"


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


def _aggregate_file_usage(result: PRTranslationResult) -> dict[str, float | int]:
    inp = out = 0
    cost = 0.0
    segments = 0
    for run in result.pair_results:
        fr = run.file_result
        if fr is None:
            continue
        inp += fr.input_tokens
        out += fr.output_tokens
        cost += fr.estimated_cost_usd
        segments += fr.segments_count
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "estimated_cost_usd": cost,
        "segments": segments,
    }


def _aggregate_models(result: PRTranslationResult) -> list[str]:
    models: set[str] = set()
    for run in result.pair_results:
        fr = run.file_result
        if fr:
            models.update(fr.models_used)
    return sorted(models)


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


def _heuristic_cell(warnings: list[str]) -> str:
    if not warnings:
        return "0"
    if len(warnings) == 1:
        return f"1 ({warnings[0][:40]}{'…' if len(warnings[0]) > 40 else ''})"
    return str(len(warnings))


def _issue_lines(issues: list[CriticIssueOut], *, applied: bool) -> list[str]:
    lines: list[str] = []
    for issue in issues:
        flag = "🟢 auto-applied" if applied else "🔴 unresolved"
        seg = issue.segment_id or "—"
        lines.append(
            f"- `{seg}` ({issue.severity}, {issue.category})\n"
            f"  - {issue.comment}\n"
            f"  - {flag}"
        )
    return lines


def _glossary_section(glossary: Glossary | None) -> str:
    if glossary is None or not glossary.entries:
        return ""
    count = len(glossary.entries)
    lines: list[str] = []
    for entry in glossary.entries[:_GLOSSARY_PREVIEW_LIMIT]:
        if entry.ru and entry.en:
            lines.append(f"- {entry.ru} → {entry.en}")
        elif entry.term:
            lines.append(f"- `{entry.term}` (do not translate)")
    if count > _GLOSSARY_PREVIEW_LIMIT:
        lines.append(f"- … and {count - _GLOSSARY_PREVIEW_LIMIT} more")
    body = "\n".join(lines)
    return (
        f"\n<details>\n<summary>Glossary used ({count} entries)</summary>\n\n"
        f"{body}\n\n</details>\n"
    )


def _usage_lines(
    config: Config,
    result: PRTranslationResult,
    usage: UsageTracker | None,
) -> list[str]:
    lines: list[str] = []
    file_usage = _aggregate_file_usage(result)

    if config.reporting.include_token_usage:
        if usage and usage.records:
            tr_in, tr_out = usage.tokens_for_role("translate")
            cr_in, cr_out = usage.tokens_for_role("critic")
            an_in, an_out = usage.tokens_for_role("analyze")
            if tr_in or tr_out:
                lines.append(f"- Tokens: translator {tr_in:,}/{tr_out:,}")
            if cr_in or cr_out:
                suffix = f"; critic {cr_in:,}/{cr_out:,}" if lines else f"- Tokens: critic {cr_in:,}/{cr_out:,}"
                if lines:
                    lines[-1] += suffix
                else:
                    lines.append(suffix)
            if an_in or an_out:
                lines.append(f"- Tokens (analyze): {an_in:,}/{an_out:,}")
            retries = usage.total_retry_count
            if retries:
                total_calls = sum(1 for r in usage.records if r.success)
                pct = (retries / max(total_calls, 1)) * 100
                lines.append(f"- Retry total: {retries} ({pct:.1f}%)")
        elif file_usage["input_tokens"] or file_usage["output_tokens"]:
            lines.append(
                f"- Tokens: {file_usage['input_tokens']:,}/{file_usage['output_tokens']:,}"
            )

    if config.reporting.include_cost:
        cost = usage.estimate_cost_usd() if usage else float(file_usage["estimated_cost_usd"])
        if cost > 0:
            lines.append(f"- Cost: ~${cost:.2f}")

    if usage:
        tr_models = usage.models_for_role("translate")
        cr_models = usage.models_for_role("critic")
        if tr_models or cr_models:
            parts: list[str] = []
            if tr_models:
                parts.append(f"translator=`{tr_models[-1]}`")
            if cr_models:
                parts.append(f"critic=`{cr_models[-1]}`")
            lines.append(f"- Models: {', '.join(parts)}")
    else:
        models = _aggregate_models(result)
        if models:
            lines.append(f"- Models: {', '.join(models)}")

    lines.append(f"- Prompt version: {config.prompts.version}")
    return lines


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
    ok, warn, blocked = _count_verdicts(result)
    total, new_count, updated_count = _file_translation_counts(result)
    status_parts: list[str] = []
    if ok:
        status_parts.append(f"{ok} OK")
    if warn:
        status_parts.append(f"{warn} требует ревью")
    if blocked:
        status_parts.append(f"{blocked} blocked")
    status = ", ".join(status_parts) if status_parts else "нет файлов"

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
    cost_line = ""
    if config.reporting.include_cost:
        cost = usage.estimate_cost_usd() if usage else float(
            _aggregate_file_usage(result)["estimated_cost_usd"]
        )
        if cost > 0:
            cost_line = f"| Стоимость | ~${cost:.2f} |\n"

    body = (
        "🤖 **ydbdoc-review** — перевод готов\n\n"
        "| | |\n"
        "|---|---|\n"
        f"| Translation PR | {tr_line} |\n"
        f"| Файлов переведено | {files_label} |\n"
        f"| Статус QA | {status} |\n"
        f"| Время | {_format_duration(meta.elapsed_s)} |\n"
        f"{cost_line}\n"
    )
    if translation_pr_number:
        body += f"👉 Полный отчёт в translation PR #{translation_pr_number}.\n"
    return body


def _file_details_section(
    run: PairRunResult,
    *,
    config: Config,
    glossary: Glossary | None,
    include_glossary: bool,
) -> str:
    fr = run.file_result
    if fr is None or run.skipped or run.deleted or run.error:
        return ""

    out = f"### {_verdict_emoji(fr.verdict)} `{run.plan.target_path}`\n\n"
    applied_n = len(fr.critic_applied)
    unresolved_n = len(fr.critic_unresolved.issues) if fr.critic_unresolved else 0
    skipped_n = len(fr.critic_skipped)

    if fr.critic_applied or fr.critic_unresolved or fr.critic_skipped:
        out += (
            f"**Critic issues (auto-applied: {applied_n}, "
            f"unresolved: {unresolved_n}"
        )
        if skipped_n:
            out += f", skipped: {skipped_n}"
        out += ")**\n"

    if fr.critic_applied:
        out += "\n".join(_issue_lines(fr.critic_applied, applied=True))
        out += "\n"
    if fr.critic_unresolved and fr.critic_unresolved.issues:
        out += "\n".join(_issue_lines(fr.critic_unresolved.issues, applied=False))
        out += "\n"
    if fr.critic_skipped:
        out += "\n".join(_issue_lines(fr.critic_skipped, applied=False))
        out += "\n"
    out += "\n"

    if fr.heuristic_warnings and config.reporting.include_heuristics:
        out += "**Heuristic warnings**\n"
        for w in fr.heuristic_warnings:
            out += f"- {w}\n"
        out += "\n"

    if include_glossary and glossary is not None:
        out += _glossary_section(glossary)

    return out


def build_full_report(
    result: PRTranslationResult,
    *,
    meta: ReportMeta,
    config: Config,
    usage: UsageTracker | None = None,
    glossary: Glossary | None = None,
) -> str:
    """Full QA report for the translation PR."""
    ok, warn, blocked = _count_verdicts(result)
    file_usage = _aggregate_file_usage(result)

    header = (
        f"🤖 **ydbdoc-review** — отчёт #{meta.report_number} "
        f"({meta.mode}, {meta.ts_label})\n\n"
    )
    emoji = _overall_verdict_emoji(ok, warn, blocked)
    parts = [ok, warn, blocked]
    labels = ["OK", "требует ревью", "blocked"]
    verdict_bits = [f"{n} {lbl}" for n, lbl in zip(parts, labels) if n]
    verdict_line = f"## Вердикт: {emoji} "
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
        status_label = fr.verdict
        if fr.verdict == "warnings":
            status_label = "Warnings"
        elif fr.verdict == "ok":
            status_label = "OK"
        table += (
            f"| `{path}` | {_verdict_emoji(fr.verdict)} {status_label} | "
            f"{applied} fixed, {unresolved} unresolved | "
            f"{_heuristic_cell(fr.heuristic_warnings)} |\n"
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

    summary = "## Сводка\n"
    summary += f"- Сегментов переведено: {file_usage['segments']} (auto-translated)\n"
    summary += f"- Critic fixes auto-applied: {applied_total}\n"
    summary += f"- Critic fixes unresolved: {unresolved_total}\n"
    summary += f"- Heuristic warnings: {hw_total}\n"
    summary += f"- Ошибок API: {result.failed_count}\n"
    summary += f"- Время: {_format_duration(meta.elapsed_s)}\n"
    summary += "\n".join(_usage_lines(config, result, usage))
    summary += "\n\n"

    details = "## Детали по файлам\n\n"
    file_runs = [
        r for r in result.pair_results
        if r.file_result and not r.skipped and not r.deleted and not r.error
    ]
    for idx, run in enumerate(file_runs):
        details += _file_details_section(
            run,
            config=config,
            glossary=glossary,
            include_glossary=(idx == len(file_runs) - 1),
        )

    footer = f"\n---\n\nGenerated by ydbdoc-review {_VERSION}\n"
    return header + verdict_line + table + "\n" + summary + details + footer
