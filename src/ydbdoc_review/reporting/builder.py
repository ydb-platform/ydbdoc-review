"""Markdown reports for source and translation PR comments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ydbdoc_review.config.loader import Config
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.pipeline.analyze import BILINGUAL_SKIP_MARKER
from ydbdoc_review.pipeline.completeness import format_completeness_gap_item, gap_label
from ydbdoc_review.pipeline.types import (
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.heuristic_context import (
    format_heuristic_location,
    heuristic_context_for_message,
)
from ydbdoc_review.reporting.heuristic_messages import (
    HeuristicReviewerDetail,
    format_critic_reviewer_detail,
    format_heuristic_reviewer_detail,
    heuristic_location_label,
    humanize_heuristic,
)
from ydbdoc_review.reporting.locations import (
    ReportLinkContext,
    consolidate_heuristic_warnings,
    filter_critic_for_report,
    format_location_label,
    manual_action_segment_ids,
)
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.schemas import CriticIssueOut
from ydbdoc_review.validation.placeholder_drift import exclude_skipped_issues
from ydbdoc_review.version import action_release_label


@dataclass(frozen=True)
class ReportMeta:
    """Header metadata for a posted report."""

    mode: str  # doc_translate | doc_verify
    report_number: int
    elapsed_s: float
    timestamp: datetime | None = None
    checkout_ref: str | None = None  # git HEAD sha of the workspace when QA ran

    @property
    def ts_label(self) -> str:
        ts = self.timestamp or datetime.now(UTC)
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
    """Count files by unified verdict (translate and verify use the same rules)."""
    ok = warn = blocked = 0
    for run in result.pair_results:
        if run.skipped or run.deleted or run.error or run.file_result is None:
            continue
        if _file_has_blocking_findings(run):
            blocked += 1
        elif _file_has_open_issues(run):
            warn += 1
        else:
            ok += 1
    return ok, warn, blocked


def _nav_has_blocking_findings(nav: NavigationRunResult) -> bool:
    """True for nav errors or blocked verdict with reviewer-visible warnings."""
    if nav.error:
        return True
    return nav.verdict == "blocked" and bool(nav.warnings)


def result_has_blocking_findings(result: PRTranslationResult) -> bool:
    """True when merge must stay 🔴: gaps, pair errors, or open blocking findings.

    Ignores stale ``file_result.verdict == "blocked"`` / nav ``verdict == "blocked"``
    when the report would list no open findings (#52055 false-RED).
    """
    if result.completeness_gaps or result.final_tree_blockers or result.failed_count:
        return True
    if any(_file_has_blocking_findings(run) for run in result.pair_results):
        return True
    return any(_nav_has_blocking_findings(nav) for nav in result.navigation_results)


def _merge_recommendation(result: PRTranslationResult) -> tuple[str, str]:
    """Return (emoji, short Russian label) for merge readiness."""
    if result.completeness_gaps:
        n = len(result.completeness_gaps)
        return (
            "🔴",
            f"не мержить — в переводном PR нет {n} ожидаемых EN-путей "
            "(см. блок ниже)",
        )
    if result.final_tree_blockers:
        return "🔴", "не мержить — QA RED, есть блокеры финального дерева"
    ok, warn, blocked = _count_verdicts(result)
    nav_blocked = any(
        _nav_has_blocking_findings(n) for n in result.navigation_results
    )
    nav_warn = any(
        n.verdict == "warnings" and not n.error for n in result.navigation_results
    )
    nav_ok = any(
        not n.error
        and (
            n.verdict == "ok"
            or (n.verdict == "blocked" and not n.warnings)
        )
        for n in result.navigation_results
    )
    if blocked or nav_blocked:
        return "🔴", "не мержить — есть блокирующие проблемы"
    if warn or nav_warn:
        return "🟡", "требует правок перед merge"
    # Nav-only PRs (e.g. #47856 toc reorder) have no markdown pair_results (§6.151).
    if ok or nav_ok:
        return "🟢", "можно мержить"
    return "⚪", "нет обработанных файлов"


def _is_new_file(run: PairRunResult) -> bool:
    summary = run.plan.summary.lower()
    return "missing" in summary or "generate from" in summary


def _bilingual_skip_count(result: PRTranslationResult) -> int:
    return sum(
        1
        for run in result.pair_results
        if run.skipped and BILINGUAL_SKIP_MARKER in run.plan.summary
    )


def _file_translation_counts(result: PRTranslationResult) -> tuple[int, int, int]:
    """Return (total translated, new, updated) including navigation YAML."""
    new = updated = 0
    for run in result.pair_results:
        if run.skipped or run.deleted or run.error or run.file_result is None:
            continue
        if _is_new_file(run):
            new += 1
        else:
            updated += 1
    nav_ok = sum(
        1 for n in result.navigation_results if n.target_text and not n.error
    )
    total = new + updated + nav_ok
    return total, new, updated


def _format_cost_rub(cost: float) -> str:
    """Human-readable RUB estimate (Yandex AI Studio sync tariffs)."""
    if cost <= 0:
        return "~₽0.00"
    if cost >= 10:
        return f"~₽{cost:.1f}"
    return f"~₽{cost:.2f}"


def _format_cost_estimate(
    *,
    usage: UsageTracker | None,
    file_usage: dict[str, float | int],
) -> str | None:
    """Return formatted cost, explicit n/a, or None when nothing to show."""
    if usage and usage.records:
        cost = usage.estimate_cost_usd()
        has_tokens = usage.has_token_usage()
    else:
        cost = float(file_usage["estimated_cost_usd"])
        has_tokens = bool(file_usage["input_tokens"] or file_usage["output_tokens"])

    if not has_tokens and cost <= 0:
        return None
    if cost > 0:
        return _format_cost_rub(cost)
    if usage and usage.is_cost_unknown():
        models = ", ".join(f"`{slug}`" for slug in usage.unpriced_models())
        if models:
            return f"n/a (модель не в прайсе: {models})"
        return "n/a (модель не в прайсе)"
    if has_tokens:
        return "n/a (модель не в прайсе)"
    return _format_cost_rub(cost)


def _aggregate_file_usage(result: PRTranslationResult) -> dict[str, float | int]:
    inp = out = 0
    cost = 0.0
    for run in result.pair_results:
        fr = run.file_result
        if fr is None:
            continue
        inp += fr.input_tokens
        out += fr.output_tokens
        cost += fr.estimated_cost_usd
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "estimated_cost_usd": cost,
    }


def _usage_section(
    config: Config,
    result: PRTranslationResult,
    usage: UsageTracker | None,
) -> str:
    """Markdown block with token usage and estimated cost."""
    lines = _usage_lines(config, result, usage)
    if not lines:
        return ""
    return "## Стоимость и токены\n\n" + "\n".join(lines) + "\n\n"


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
            role_lines = 0
            if tr_in or tr_out:
                lines.append(f"- Токены (перевод): {tr_in:,} / {tr_out:,}")
                role_lines += 1
            if cr_in or cr_out:
                lines.append(f"- Токены (критик): {cr_in:,} / {cr_out:,}")
                role_lines += 1
            if an_in or an_out:
                lines.append(f"- Токены (analyze): {an_in:,} / {an_out:,}")
                role_lines += 1
            total_in = usage.total_input_tokens
            total_out = usage.total_output_tokens
            if total_in or total_out:
                lines.append(f"- Токены (всего): {total_in:,} / {total_out:,}")
            retries = usage.total_retry_count
            if retries:
                total_calls = sum(1 for r in usage.records if r.success)
                pct = (retries / max(total_calls, 1)) * 100
                lines.append(f"- Повторы LLM: {retries} ({pct:.1f}%)")
        elif file_usage["input_tokens"] or file_usage["output_tokens"]:
            lines.append(
                f"- Токены: {file_usage['input_tokens']:,} / "
                f"{file_usage['output_tokens']:,}"
            )

    if config.reporting.include_cost:
        cost_label = _format_cost_estimate(usage=usage, file_usage=file_usage)
        if cost_label:
            lines.append(f"- Оценка стоимости: {cost_label}")

    if usage:
        tr_models = usage.models_for_role("translate")
        cr_models = usage.models_for_role("critic")
        if tr_models or cr_models:
            parts: list[str] = []
            if tr_models:
                parts.append(f"перевод=`{tr_models[-1]}`")
            if cr_models:
                parts.append(f"критик=`{cr_models[-1]}`")
            lines.append(f"- Модели: {', '.join(parts)}")

    return lines


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
    """Issues the reviewer still needs to look at (unresolved after apply)."""
    if not fr.critic_unresolved:
        return []
    return exclude_skipped_issues(
        list(fr.critic_unresolved.issues),
        list(fr.critic_skipped),
    )


def _skipped_critic_issues(fr) -> list[CriticIssueOut]:
    """Critic suggestions that were not auto-applied (safety / validation)."""
    return list(fr.critic_skipped)


def _lang_label(code: str) -> str:
    lowered = code.lower()
    if lowered in {"ru", "russian"}:
        return "RU"
    if lowered in {"en", "english"}:
        return "EN"
    return code.upper()


def _format_reviewer_item(
    *,
    index: int,
    location: str,
    problem: str,
    severity: str | None = None,
    source_excerpt: str | None = None,
    target_excerpt: str | None = None,
    source_lang: str = "ru",
    target_lang: str = "en",
    suggestion: str | None = None,
) -> str:
    lines = [f"{index}. **{location}**"]
    if source_excerpt:
        lines.append(
            f"   - **Оригинал ({_lang_label(source_lang)}):** «{source_excerpt}»"
        )
    if target_excerpt:
        lines.append(f"   - **Перевели:** «{target_excerpt}»")
    lines.append(f"   - **Проблема:** {problem}")
    if suggestion:
        preview = suggestion.replace("\n", " ")
        if len(preview) > 320:
            preview = preview[:317] + "…"
        lines.append(f"   - **Совет:** {preview}")
    return "\n".join(lines)


def _format_critic_item(
    issue: CriticIssueOut,
    segment_locations: dict[str, str],
    *,
    index: int,
    file_path: str,
    segment_lines: dict[str, tuple[int, int]],
    segment_excerpts: dict[str, str],
    segment_source_excerpts: dict[str, str],
    source_lang: str,
    target_lang: str,
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
    critic_detail = format_critic_reviewer_detail(
        category=issue.category,
        comment=issue.comment or "",
    )
    if issue.category in {"critic_execution_failed", "critic_model_refusal"}:
        problem = critic_detail.problem
        suggestion = critic_detail.suggestion
    else:
        category = issue.category.replace("_", " ")
        problem = f"({category}) {issue.comment}"
        suggestion = issue.suggested_text
    source_excerpt = (
        segment_source_excerpts.get(issue.segment_id) if issue.segment_id else None
    )
    target_excerpt = (
        segment_excerpts.get(issue.segment_id) if issue.segment_id else None
    )
    return _format_reviewer_item(
        index=index,
        location=location,
        problem=problem,
        severity=issue.severity,
        source_excerpt=source_excerpt,
        target_excerpt=target_excerpt,
        source_lang=source_lang,
        target_lang=target_lang,
        suggestion=suggestion,
    )


def _report_heuristic_messages(fr, *, config: Config) -> list[str]:
    """Blocking + non-blocking heuristics for the reviewer section (not info)."""
    if not config.reporting.include_heuristics:
        return list(fr.heuristic_blocking)
    return [*fr.heuristic_blocking, *fr.heuristic_warnings]


def _file_has_open_issues(run: PairRunResult) -> bool:
    fr = run.file_result
    if fr is None:
        return False
    if fr.segment_alignment_error:
        return True
    if _remaining_critic_issues(fr):
        return True
    if fr.manual_actions:
        return True
    if fr.heuristic_blocking:
        return True
    return bool(fr.heuristic_warnings)


def _file_has_blocking_findings(run: PairRunResult) -> bool:
    """True when the file still has reviewer-visible blocking findings.

    Stale ``verdict == "blocked"`` with empty heuristics / no remaining blocked
    critic issues must not count (QA list would be all 🟢).
    """
    if run.skipped or run.deleted or run.error:
        return False
    fr = run.file_result
    if fr is None:
        return False
    if fr.segment_alignment_error:
        return True
    if fr.heuristic_blocking:
        return True
    return any(
        issue.severity == "blocked" for issue in _remaining_critic_issues(fr)
    )


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
    manual_ranges = [
        fr.segment_lines[mid]
        for mid in manual_ids
        if mid in fr.segment_lines
    ]
    heuristics = consolidate_heuristic_warnings(
        _report_heuristic_messages(fr, config=config),
        manual_ids=manual_ids,
        manual_line_ranges=manual_ranges,
    )

    file_path = run.plan.target_path
    source_lang = run.plan.source_lang
    target_lang = run.plan.target_lang

    if fr.segment_alignment_error:
        out = f"### 🔴 `{file_path}`\n\n"
        out += _format_reviewer_item(
            index=item_index,
            location="сегменты RU/EN",
            problem=(
                f"(alignment) EN не совпадает со структурой RU: "
                f"{fr.segment_alignment_error}"
            ),
            severity="blocked",
            source_lang=source_lang,
            target_lang=target_lang,
        ) + "\n\n"
        return out, item_index + 1

    if not critic_items and not heuristics and not manual_actions:
        skipped = _skipped_critic_issues(fr)
        if skipped and config.reporting.include_skipped_critic:
            out = f"### {_verdict_emoji(fr.verdict)} `{file_path}`\n\n"
            out += (
                "<details>\n<summary>Автоисправление не применено "
                f"({len(skipped)} — отклонено защитой pipeline)</summary>\n\n"
            )
            for issue in skipped:
                out += (
                    _format_critic_item(
                        issue,
                        fr.segment_locations,
                        index=item_index,
                        file_path=file_path,
                        segment_lines=fr.segment_lines,
                        segment_excerpts=fr.segment_excerpts,
                        segment_source_excerpts=fr.segment_source_excerpts,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        link=link,
                    )
                    + "\n\n"
                )
                item_index += 1
            out += "</details>\n\n"
            return out, item_index
        if fr.verdict == "ok":
            out = f"### 🟢 `{file_path}`\n\n"
            out += "Замечаний нет.\n\n"
            return out, item_index
        return "", item_index

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
            severity="blocked",
            source_excerpt=fr.segment_source_excerpts.get(action.segment_id),
            target_excerpt=fr.segment_excerpts.get(action.segment_id),
            source_lang=source_lang,
            target_lang=target_lang,
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
                segment_excerpts=fr.segment_excerpts,
                segment_source_excerpts=fr.segment_source_excerpts,
                source_lang=source_lang,
                target_lang=target_lang,
                link=link,
            )
            + "\n\n"
        )
        item_index += 1
    skipped = _skipped_critic_issues(fr)
    if skipped and config.reporting.include_skipped_critic:
        out += (
            "<details>\n<summary>Автоисправление не применено "
            f"({len(skipped)} — отклонено защитой pipeline)</summary>\n\n"
        )
        for issue in skipped:
            out += (
                _format_critic_item(
                    issue,
                    fr.segment_locations,
                    index=item_index,
                    file_path=file_path,
                    segment_lines=fr.segment_lines,
                    segment_excerpts=fr.segment_excerpts,
                    segment_source_excerpts=fr.segment_source_excerpts,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    link=link,
                )
                + "\n\n"
            )
            item_index += 1
        out += "</details>\n\n"
    for warning in heuristics:
        blocking = warning in fr.heuristic_blocking
        detail = format_heuristic_reviewer_detail(warning)
        ctx = heuristic_context_for_message(
            warning,
            target_text=fr.final_text,
            segment_source_excerpts=fr.segment_source_excerpts,
        )
        line_hint = ""
        if ctx.line_range:
            start = ctx.line_range[0]
            line_hint = f"в `{file_path}` около строки {start}."
            if detail.suggestion and line_hint not in detail.suggestion:
                detail = HeuristicReviewerDetail(
                    problem=detail.problem,
                    suggestion=f"{detail.suggestion} Исправьте {line_hint}",
                )
        location = format_heuristic_location(
            warning,
            file_path=file_path,
            link=link,
            line_range=ctx.line_range,
            default_label=heuristic_location_label(warning),
        )
        out += _format_reviewer_item(
            index=item_index,
            location=location,
            problem=detail.problem,
            severity="blocked" if blocking else "warning",
            source_excerpt=ctx.source_excerpt,
            target_excerpt=ctx.target_excerpt,
            source_lang=source_lang,
            target_lang=target_lang,
            suggestion=detail.suggestion,
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
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"Apply critic fixes from doc_verify run on {ts}\n\n"
            f"Critic: {critic_model}\n"
            f"Fixed segments: {fixed}\n"
            f"{action_release_label()}\n"
        )

    translated = [
        r for r in result.pair_results
        if r.file_result and not r.skipped and not r.deleted
    ]
    paths = [r.plan.target_path for r in translated if r.target_text is not None]
    paths.extend(
        n.en_path
        for n in result.navigation_results
        if n.target_text is not None and not n.error
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
            action_release_label(),
        ]
    )
    return "\n".join(lines)


def build_translation_pr_body(
    source_pr: int,
    source_repo: str,
    *,
    publication_result: PRTranslationResult | None = None,
) -> str:
    red = bool(
        publication_result
        and publication_result.publication_impact == PublicationImpact.PUBLISH_RED
    )
    banner = ""
    blockers = ""
    if red and publication_result is not None:
        banner = (
            "> [!CAUTION]\n"
            "> **QA RED, do not merge.** Candidate опубликован для ручного исправления.\n\n"
        )
        blockers = "\n\n**Final-tree blockers:**\n\n" + "\n".join(
            f"- `{blocker.path}`: {blocker.message.replace(chr(10), ' ')}"
            for blocker in publication_result.final_tree_blockers
        )
    return (
        f"{banner}"
        f"Auto-generated translation for [{source_repo}#{source_pr}]"
        f"(https://github.com/{source_repo}/pull/{source_pr}).\n\n"
        f"Branch: `ydbdoc-review/pr-{source_pr}`\n\n"
        "QA (`doc_verify`) runs inline in the same `doc_translate` CI job; "
        "re-run manually via the **`doc_verify`** label (`ydbdoc-verify.yml`)."
        f"{blockers}"
    )


def build_translate_handoff_comment(
    result: PRTranslationResult,
    *,
    source_pr: int,
    source_repo: str,
    meta: ReportMeta,
    config: Config,
    usage: UsageTracker | None = None,
) -> str:
    """Legacy short comment — superseded by inline ``doc_verify`` (§6.73)."""
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

    cost_line = ""
    if config.reporting.include_cost:
        cost_label = _format_cost_estimate(
            usage=usage,
            file_usage=_aggregate_file_usage(result),
        )
        if cost_label:
            cost_line = f"| Стоимость перевода | {cost_label} |\n"

    checkout_line = ""
    if meta.checkout_ref:
        checkout_line = f"Checkout: `{meta.checkout_ref[:12]}`\n\n"

    source_url = f"https://github.com/{source_repo}/pull/{source_pr}"
    return (
        f"🤖 **ydbdoc-review** — перевод выполнен "
        f"(отчёт №{meta.report_number}, {meta.ts_label})\n\n"
        f"{checkout_line}"
        f"Исходный PR: [#{source_pr}]({source_url})\n\n"
        "| | |\n"
        "|---|---|\n"
        f"| Файлов | {files_label} |\n"
        f"| Время | {_format_duration(meta.elapsed_s)} |\n"
        f"{cost_line}\n"
        "**Следующий шаг:** job **`ydbdoc-verify-auto`** в том же workflow запустит "
        "**`doc_verify`** (critic + эвристики + вердикт). Полный QA-отчёт появится "
        "в комментарии ниже. Повторная проверка — лейбл **`doc_verify`** "
        "(`ydbdoc-verify.yml`)."
    )


def build_verify_fixup_pr_body(source_pr: int, source_repo: str, branch: str) -> str:
    return (
        f"Auto-generated critic fixes for [{source_repo}#{source_pr}]"
        f"(https://github.com/{source_repo}/pull/{source_pr}).\n\n"
        f"For author/fork PRs critic fixes use a separate branch/PR — the workflow "
        f"never pushes commits onto the verified PR head (see §6.64). "
        f"Translation PRs use inline push (§6.75).\n\n"
        f"The full ``doc_verify`` QA report is posted on **this** PR (§6.146). "
        f"Use ``doc_continue`` here to iterate; do not expect the report on the "
        f"source PR once a fixup exists.\n\n"
        f"Branch: `{branch}`\n"
    )


def build_verify_fixup_source_comment(
    fixup_pr_number: int,
    *,
    translation_pr: bool = False,
) -> str:
    """Comment on the verified PR pointing at the critic-fixup PR.

    Translation PRs: merge/cherry-pick into the translation branch.
    Bilingual/author PRs: merge the fixup PR (usually into ``main`` / the
    verified base) or cherry-pick onto the author branch — never «ветка перевода».

    Full QA report lives on the fixup PR (§6.146); this comment is a short pointer.
    """
    if translation_pr:
        how = "Замёрджите его в ветку перевода или cherry-pick'ните коммиты."
    else:
        how = (
            "Это **не** translation PR: замёрджите fixup-PR "
            "(обычно в базовую ветку проверенного PR, чаще ``main``) "
            "или cherry-pick'ните коммиты в авторскую ветку."
        )
    return (
        "🤖 **ydbdoc-review** — критик предложил правки\n\n"
        f"Правки и **полный QA-отчёт** — в #{fixup_pr_number}.\n\n"
        f"{how}\n\n"
        f"Дальше: ``/ydbdoc continue …`` + лейбл ``doc_continue`` "
        f"**на #{fixup_pr_number}** (§6.146)."
    )


def _withhold_source_details(result: PRTranslationResult) -> list[tuple[str, str]]:
    """Return concrete affected paths/reasons for a withheld source summary."""
    details: list[tuple[str, str]] = []
    for run in result.pair_results:
        reasons: list[str] = []
        if run.error:
            reasons.append(run.error)
        if (
            run.plan.action not in {"skip", "delete_en"}
            and not run.skipped
            and not run.deleted
            and run.target_text is None
        ):
            reasons.append("expected EN output отсутствует")
        fr = run.file_result
        if fr is not None:
            if fr.segment_alignment_error:
                reasons.append(fr.segment_alignment_error)
            reasons.extend(issue.message for issue in run.validation_issues)
            reasons.extend(issue.message for issue in fr.link_contract_issues)
            reasons.extend(action.message for action in fr.manual_actions)
            reasons.extend(fr.heuristic_blocking)
            reasons.extend(
                warning
                for warning in fr.heuristic_warnings
                if warning.startswith("translate_soft_keep:")
            )
        if not reasons and result.publication_impact in {
            PublicationImpact.WITHHOLD_INCOMPLETE,
            PublicationImpact.WITHHOLD_UNSAFE,
        }:
            reasons.append(result.publication_impact.value)
        for reason in dict.fromkeys(reasons):
            details.append((run.plan.target_path, str(reason).replace("\n", " ")))
    for nav in result.navigation_results:
        reasons = [*(nav.warnings or ())]
        if nav.error:
            reasons.append(nav.error)
        if nav.verdict == "blocked" and not reasons:
            reasons.append("mandatory navigation validation blocked")
        for reason in dict.fromkeys(reasons):
            details.append((nav.en_path, str(reason).replace("\n", " ")))
    return list(dict.fromkeys(details))


def build_source_pr_comment(
    result: PRTranslationResult,
    *,
    translation_pr_number: int | None,
    meta: ReportMeta,
    config: Config,
    usage: UsageTracker | None = None,
    verify_result: PRTranslationResult | None = None,
    committed: bool | None = None,
) -> str:
    """Short summary comment for the source PR after ``doc_translate``."""
    total, new_count, updated_count = _file_translation_counts(result)
    bilingual_skip = _bilingual_skip_count(result)
    published_red = result.publication_impact == PublicationImpact.PUBLISH_RED

    if total == 0 and bilingual_skip and translation_pr_number is None:
        pairs_label = (
            "1 bilingual-пара"
            if bilingual_skip == 1
            else f"{bilingual_skip} bilingual-пар"
        )
        return (
            "🤖 **ydbdoc-review** — перевод не требуется\n\n"
            f"В source PR обновлены обе стороны ({pairs_label}); "
            f"автоперевод пропущен ({BILINGUAL_SKIP_MARKER}). "
            "Translation PR не создаётся.\n\n"
            f"| Время | {_format_duration(meta.elapsed_s)} |\n"
        )

    if (
        result.completeness_gaps
        or result.publication_impact
        in {PublicationImpact.WITHHOLD_INCOMPLETE, PublicationImpact.WITHHOLD_UNSAFE}
        or (published_red and translation_pr_number is None)
        or (translation_pr_number is None and committed is True)
    ):
        failure_label = (
            "completeness gaps"
            if result.completeness_gaps
            else "publication failed"
        )
        body = (
            "🤖 **ydbdoc-review** — translation PR **не создан**\n\n"
            "Publication заблокирована: candidate incomplete/unsafe или RED artifact "
            "не удалось опубликовать (§6.80 completeness gate, R-GL-12).\n\n"
            "Автоперевод **работает** для обычных пар `docs/ru/…` ↔ `docs/en/…`. "
            "Ниже — файлы, которые pipeline не смог довести до EN в этом прогоне.\n\n"
            "| | |\n"
            "|---|---|\n"
            f"| Translation PR | — |\n"
            f"| Время | {_format_duration(meta.elapsed_s)} |\n"
            f"| Статус | 🔴 не мержить — {failure_label} |\n\n"
            "**Не переведены:**\n\n"
        )
        for path in result.completeness_gaps:
            body += f"- {gap_label(path)}\n"
        for blocker in result.final_tree_blockers:
            body += f"- `{blocker.path}`: {blocker.message.replace(chr(10), ' ')}\n"
        errors = [r for r in result.pair_results if r.error]
        if errors:
            body += "\n**Ошибки pipeline:**\n\n"
            for run in errors:
                body += f"- `{run.plan.target_path}`: {run.error}\n"
        withhold_details = _withhold_source_details(result)
        if withhold_details:
            action = (
                "добавить отсутствующий output и повторить `doc_translate`"
                if result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
                else "исправить structural/integrity blocker и повторить `doc_translate`"
            )
            body += "\n**Почему публикация удержана:**\n\n"
            for path, reason in withhold_details:
                body += f"- `{path}`: {reason}. Действие: {action}.\n"
        if result.yellow_warnings:
            body += "\n**Жёлтые предупреждения (не блокируют):**\n\n"
            for warning in result.yellow_warnings:
                body += f"- {warning}\n"
        if config.reporting.include_cost:
            cost_label = _format_cost_estimate(
                usage=usage,
                file_usage=_aggregate_file_usage(result),
            )
            if cost_label:
                body += f"\n| Стоимость перевода | {cost_label} |\n"
        return body

    # No translation PR and nothing to push: RU toc reorder / no-op merge (§6.141).
    if translation_pr_number is None and (
        committed is False or (committed is not True and total == 0)
    ):
        cost_line = ""
        if config.reporting.include_cost:
            cost_label = _format_cost_estimate(
                usage=usage,
                file_usage=_aggregate_file_usage(result),
            )
            if cost_label:
                cost_line = f"| Стоимость перевода | {cost_label} |\n"
        return (
            "🤖 **ydbdoc-review** — перевод не требуется\n\n"
            "После scoped merge EN совпадает с `main` "
            "(нет коммита / Translation PR не создаётся). "
            "Типичный случай: перестановка пунктов toc, которых нет на EN, "
            "или RU-only правки без изменений зеркала (§6.141).\n\n"
            "| | |\n"
            "|---|---|\n"
            f"| Translation PR | — |\n"
            f"| Файлов | {total} |\n"
            f"| Время | {_format_duration(meta.elapsed_s)} |\n"
            f"{cost_line}"
        )

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
        cost_label = _format_cost_estimate(
            usage=usage,
            file_usage=_aggregate_file_usage(result),
        )
        if cost_label:
            cost_line = f"| Стоимость перевода | {cost_label} |\n"

    qa_line = ""
    if translation_pr_number:
        if published_red:
            qa_line = "| Статус QA | 🔴 published_red, не мержить |\n"
        elif result.completeness_gaps:
            n = len(result.completeness_gaps)
            qa_line = (
                f"| Статус QA | 🔴 не мержить — в переводном PR нет {n} "
                "ожидаемых EN-путей |\n"
            )
        elif verify_result is not None:
            qa_emoji, qa_label = _merge_recommendation(verify_result)
            qa_line = f"| Статус QA | {qa_emoji} {qa_label} |\n"

    headline = (
        "🤖 **ydbdoc-review** — published_red, QA RED, не мержить"
        if published_red
        else "🤖 **ydbdoc-review** — перевод готов"
    )
    body = (
        f"{headline}\n\n"
        "| | |\n"
        "|---|---|\n"
        f"| Translation PR | {tr_line} |\n"
        f"| Файлов | {files_label} |\n"
        f"| Время | {_format_duration(meta.elapsed_s)} |\n"
        f"{cost_line}"
        f"{qa_line}\n"
    )
    if translation_pr_number:
        body += (
            f"Полный QA-отчёт — в комментарии к translation PR #{translation_pr_number}. "
            "Повторная проверка — лейбл **`doc_verify`** (`ydbdoc-verify.yml`).\n"
        )
    elif bilingual_skip:
        body += (
            f"\n{bilingual_skip} пар(ы) пропущены — bilingual update в source PR "
            f"({BILINGUAL_SKIP_MARKER}).\n"
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
    del glossary
    rec_emoji, rec_label = _merge_recommendation(result)

    checkout_line = ""
    if meta.checkout_ref:
        short = meta.checkout_ref[:12]
        checkout_line = f"Checkout: `{short}`\n\n"
    header = (
        f"🤖 **ydbdoc-review** — отчёт №{meta.report_number} "
        f"({meta.mode}, {meta.ts_label})\n\n"
        f"{checkout_line}"
        f"## Рекомендация: {rec_emoji} {rec_label}\n\n"
    )

    file_runs = [
        r for r in result.pair_results
        if r.file_result and not r.skipped and not r.deleted and not r.error
    ]
    problem_runs = [r for r in file_runs if _file_has_open_issues(r)]
    ok_runs = [r for r in file_runs if not _file_has_open_issues(r)]

    nav_runs = [n for n in result.navigation_results if not n.error]
    nav_problems = [
        n for n in nav_runs if n.warnings or n.verdict == "warnings"
    ]
    nav_ok = [
        n for n in nav_runs if not n.warnings and n.verdict != "warnings"
    ]

    completeness_section = ""
    if result.completeness_gaps:
        completeness_section = (
            "## Что исправить: ожидаемые EN-пути отсутствуют в diff PR\n\n"
            "Это **не** обязательно «файла нет на main». Часто путь — "
            "зависимость scope: EN уже есть на tip, но **не попал в commit** "
            "переводной ветки.\n\n"
        )
        for i, path in enumerate(result.completeness_gaps, start=1):
            completeness_section += (
                f"{i}. {format_completeness_gap_item(path)}\n\n"
            )

    final_tree_section = ""
    if result.final_tree_blockers:
        final_tree_section = (
            "## QA RED, do not merge: блокеры финального дерева\n\n"
            "Candidate опубликован для ручного исправления, но merge запрещён.\n\n"
        )
        for i, blocker in enumerate(result.final_tree_blockers, start=1):
            final_tree_section += (
                f"{i}. `{blocker.path}`: {blocker.message.replace(chr(10), ' ')}\n\n"
            )

    yellow_section = ""
    if result.yellow_warnings:
        yellow_section = (
            "## Жёлтые предупреждения (не блокируют commit/push)\n\n"
        )
        for i, warning in enumerate(result.yellow_warnings, start=1):
            yellow_section += f"{i}. {warning}\n\n"

    if not file_runs and not nav_runs:
        errors = [r for r in result.pair_results if r.error]
        nav_errors = [n for n in result.navigation_results if n.error]
        if errors or nav_errors:
            body = (
                header
                + completeness_section
                + final_tree_section
                + yellow_section
                + "## Ошибки pipeline\n\n"
            )
            for run in errors:
                body += f"- `{run.plan.target_path}`: {run.error}\n"
            for nav in nav_errors:
                body += f"- `{nav.en_path}`: {nav.error}\n"
            body += f"\n---\n\nGenerated by {action_release_label()}\n"
            return body
        if completeness_section or final_tree_section or yellow_section:
            usage_block = _usage_section(config, result, usage)
            body = header + completeness_section + final_tree_section + yellow_section
            if usage_block:
                body += usage_block
            return body + f"---\n\nGenerated by {action_release_label()}\n"
        return header + "Нет обработанных файлов.\n"

    body = header + completeness_section + final_tree_section + yellow_section
    if not problem_runs and not nav_problems:
        if completeness_section:
            body += (
                "В уже обработанных файлах открытых замечаний критика нет — "
                "блокер только в completeness выше.\n\n"
            )
        elif final_tree_section:
            body += (
                "В файловых результатах открытых замечаний критика нет — "
                "merge блокируют проверки финального дерева выше.\n\n"
            )
        else:
            body += "По всем файлам открытых замечаний нет.\n\n"
        green_lines: list[str] = []
        item_index = 1
        for run in ok_runs:
            fr = run.file_result
            if (
                fr
                and _skipped_critic_issues(fr)
                and config.reporting.include_skipped_critic
            ):
                section, item_index = _file_reviewer_section(
                    run, config=config, item_index=item_index, link=link
                )
                body += section
            else:
                green_lines.append(f"- 🟢 `{run.plan.target_path}`")
        for nav in nav_ok:
            green_lines.append(f"- 🟢 `{nav.en_path}` (навигация)")
        if green_lines and completeness_section:
            body += (
                "<details>\n<summary>Прочие проверенные файлы без замечаний "
                f"({len(green_lines)})</summary>\n\n"
                + "\n".join(green_lines)
                + "\n\n</details>\n\n"
            )
        elif green_lines:
            body += "\n".join(green_lines) + "\n\n"
        info_lines = []
        for run in file_runs:
            fr = run.file_result
            if fr is None or not fr.heuristic_info:
                continue
            for msg in fr.heuristic_info:
                info_lines.append(f"- `{run.plan.target_path}` — {msg}")
        if info_lines:
            body += "## Справка (не блокирует merge EN)\n\n"
            body += "\n".join(info_lines) + "\n\n"
        usage_block = _usage_section(config, result, usage)
        if usage_block:
            body += usage_block
        body += f"---\n\nGenerated by {action_release_label()}\n"
        return body

    body += (
        "## Что исправить в обработанных файлах\n\n"
        if completeness_section
        else "## Что исправить\n\n"
    )
    item_index = 1
    for run in problem_runs:
        section, item_index = _file_reviewer_section(
            run, config=config, item_index=item_index, link=link
        )
        body += section
    for nav in nav_problems:
        emoji = _verdict_emoji(nav.verdict)
        body += f"### {emoji} `{nav.en_path}` (навигация)\n\n"
        for w in nav.warnings:
            body += (
                f"{item_index}. **{heuristic_location_label(w)}** — "
                f"{humanize_heuristic(w)}\n\n"
            )
            item_index += 1

    errors = [r for r in result.pair_results if r.error]
    if errors:
        body += "## Ошибки pipeline\n\n"
        for run in errors:
            body += f"- `{run.plan.target_path}`: {run.error}\n"
        body += "\n"

    if ok_runs or nav_ok:
        body += "## Без замечаний\n\n"
        for run in ok_runs:
            body += f"- 🟢 `{run.plan.target_path}`\n"
        for nav in nav_ok:
            body += f"- 🟢 `{nav.en_path}` (навигация)\n"
        body += "\n"

    info_lines: list[str] = []
    for run in file_runs:
        fr = run.file_result
        if fr is None or not fr.heuristic_info:
            continue
        for msg in fr.heuristic_info:
            info_lines.append(f"- `{run.plan.target_path}` — {msg}")
    if info_lines:
        body += "## Справка (не блокирует merge EN)\n\n"
        body += "\n".join(info_lines) + "\n\n"

    usage_block = _usage_section(config, result, usage)
    if usage_block:
        body += usage_block

    body += f"---\n\nGenerated by {action_release_label()}\n"
    return body
