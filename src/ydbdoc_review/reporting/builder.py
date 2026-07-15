"""Markdown reports for source and translation PR comments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ydbdoc_review.config.loader import Config
from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.pipeline.analyze import BILINGUAL_SKIP_MARKER
from ydbdoc_review.pipeline.completeness import gap_label
from ydbdoc_review.pipeline.types import PRTranslationResult, PairRunResult
from ydbdoc_review.reporting.heuristic_context import (
    format_heuristic_location,
    heuristic_context_for_message,
)
from ydbdoc_review.reporting.heuristic_messages import (
    HeuristicReviewerDetail,
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
    """Count files by unified verdict (translate and verify use the same rules)."""
    ok = warn = blocked = 0
    for run in result.pair_results:
        if run.skipped or run.deleted or run.error or run.file_result is None:
            continue
        fr = run.file_result
        if fr.verdict == "blocked":
            blocked += 1
        elif _file_has_open_issues(run):
            warn += 1
        else:
            ok += 1
    return ok, warn, blocked


def _merge_recommendation(result: PRTranslationResult) -> tuple[str, str]:
    """Return (emoji, short Russian label) for merge readiness."""
    if result.completeness_gaps:
        return "🔴", "не мержить — не все файлы source PR переведены"
    ok, warn, blocked = _count_verdicts(result)
    nav_blocked = any(
        n.verdict == "blocked" or n.error for n in result.navigation_results
    )
    nav_warn = any(
        n.verdict == "warnings" and not n.error for n in result.navigation_results
    )
    if blocked or nav_blocked:
        return "🔴", "не мержить — есть блокирующие проблемы"
    if warn or nav_warn:
        return "🟡", "требует правок перед merge"
    if ok:
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
    category = issue.category.replace("_", " ")
    problem = f"({category}) {issue.comment}"
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
        suggestion=issue.suggested_text,
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
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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


def build_translation_pr_body(source_pr: int, source_repo: str) -> str:
    return (
        f"Auto-generated translation for [{source_repo}#{source_pr}]"
        f"(https://github.com/{source_repo}/pull/{source_pr}).\n\n"
        f"Branch: `ydbdoc-review/pr-{source_pr}`\n\n"
        "QA (`doc_verify`) runs inline in the same `doc_translate` CI job; "
        "re-run manually via the **`doc_verify`** label (`ydbdoc-verify.yml`)."
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
        f"(отчёт #{meta.report_number}, {meta.ts_label})\n\n"
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
        f"Branch: `{branch}`\n"
    )


def build_verify_fixup_source_comment(fixup_pr_number: int) -> str:
    return (
        "🤖 **ydbdoc-review** — критик предложил правки\n\n"
        f"Правки оформлены отдельным PR: #{fixup_pr_number}.\n\n"
        "Замёрджите его в ветку перевода или cherry-pick'ните коммиты."
    )


def build_source_pr_comment(
    result: PRTranslationResult,
    *,
    translation_pr_number: int | None,
    meta: ReportMeta,
    config: Config,
    usage: UsageTracker | None = None,
    verify_result: PRTranslationResult | None = None,
) -> str:
    """Short summary comment for the source PR after ``doc_translate``."""
    total, new_count, updated_count = _file_translation_counts(result)
    bilingual_skip = _bilingual_skip_count(result)

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

    if result.completeness_gaps and translation_pr_number is None:
        body = (
            "🤖 **ydbdoc-review** — translation PR **не создан**\n\n"
            "Push заблокирован: не все EN-зеркала source PR переведены "
            "(§6.80 completeness gate).\n\n"
            "Автоперевод **работает** для обычных пар `docs/ru/…` ↔ `docs/en/…`. "
            "Ниже — файлы, которые pipeline не смог довести до EN в этом прогоне.\n\n"
            "| | |\n"
            "|---|---|\n"
            f"| Translation PR | — |\n"
            f"| Время | {_format_duration(meta.elapsed_s)} |\n"
            "| Статус | 🔴 не мержить — completeness gaps |\n\n"
            "**Не переведены:**\n\n"
        )
        for path in result.completeness_gaps:
            body += f"- {gap_label(path)}\n"
        errors = [r for r in result.pair_results if r.error]
        if errors:
            body += "\n**Ошибки pipeline:**\n\n"
            for run in errors:
                body += f"- `{run.plan.target_path}`: {run.error}\n"
        if config.reporting.include_cost:
            cost_label = _format_cost_estimate(
                usage=usage,
                file_usage=_aggregate_file_usage(result),
            )
            if cost_label:
                body += f"\n| Стоимость перевода | {cost_label} |\n"
        return body

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
        if result.completeness_gaps:
            qa_line = "| Статус QA | 🔴 не мержить — не все файлы source PR переведены |\n"
        elif verify_result is not None:
            qa_emoji, qa_label = _merge_recommendation(verify_result)
            qa_line = f"| Статус QA | {qa_emoji} {qa_label} |\n"

    body = (
        "🤖 **ydbdoc-review** — перевод готов\n\n"
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
        f"🤖 **ydbdoc-review** — отчёт #{meta.report_number} "
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
        n for n in nav_runs if n.warnings or n.verdict != "ok"
    ]
    nav_ok = [n for n in nav_runs if not n.warnings and n.verdict == "ok"]

    if result.completeness_gaps:
        body = header + "## Что исправить\n\n"
        for i, path in enumerate(result.completeness_gaps, start=1):
            body += f"{i}. **{gap_label(path)}**\n\n"
        usage_block = _usage_section(config, result, usage)
        if usage_block:
            body += usage_block
        body += f"---\n\nGenerated by {action_release_label()}\n"
        return body

    if not file_runs and not nav_runs:
        errors = [r for r in result.pair_results if r.error]
        nav_errors = [n for n in result.navigation_results if n.error]
        if errors or nav_errors:
            body = header + "## Ошибки pipeline\n\n"
            for run in errors:
                body += f"- `{run.plan.target_path}`: {run.error}\n"
            for nav in nav_errors:
                body += f"- `{nav.en_path}`: {nav.error}\n"
            body += f"\n---\n\nGenerated by {action_release_label()}\n"
            return body
        return header + "Нет обработанных файлов.\n"

    body = header
    if not problem_runs and not nav_problems:
        body += "По всем файлам открытых замечаний нет.\n\n"
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
                body += f"- 🟢 `{run.plan.target_path}`\n"
        for nav in nav_ok:
            body += f"- 🟢 `{nav.en_path}` (навигация)\n"
        body += "\n"
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

    body += "## Что исправить\n\n"
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
