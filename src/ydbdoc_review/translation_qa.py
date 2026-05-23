"""Thin wrapper around :mod:`ydbdoc_review.pipeline_v2`.

QA / repair / report formatting live in ``pipeline_v2``; this module only
re-exports the names used by callers (CLI and tests) and provides a small
batch helper :func:`run_pairs_qa_and_repair`.
"""

from __future__ import annotations

from ydbdoc_review import git_local
from ydbdoc_review.config import Settings
from ydbdoc_review.pipeline_v2 import (
    PairQaOutcome,
    apply_fix_diff,
    final_verdict,
    format_pair_qa_markdown,
    format_translation_pr_summary,
    parse_verdict,
    run_pair_qa,
    run_pair_qa_repair,
    VERDICT_ACCEPT,
    VERDICT_ACCEPT_WITH_NOTES,
    VERDICT_ERROR,
    VERDICT_REJECT,
)


__all__ = [
    "PairQaOutcome",
    "VERDICT_ACCEPT",
    "VERDICT_ACCEPT_WITH_NOTES",
    "VERDICT_ERROR",
    "VERDICT_REJECT",
    "apply_fix_diff",
    "final_verdict",
    "format_pair_qa_markdown",
    "format_translation_pr_summary",
    "parse_verdict",
    "run_pair_qa",
    "run_pair_qa_repair",
    "run_pairs_qa_and_repair",
]


def run_pairs_qa_and_repair(
    settings: Settings,
    *,
    workdir: str,
    pairs: list[tuple[str, str]],
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]],
    source_pr_number: int | None,
    base_ref_local: str | None,
    repair_enabled: bool | None = None,
) -> tuple[str | None, list[str], list[PairQaOutcome]]:
    """Run QA for every (ru_path, en_path) pair; write repaired EN to *workdir*.

    Returns ``(comment_markdown, repaired_en_paths, outcomes)``.
    """
    if not pairs:
        return None, [], []
    repair_on = (
        settings.translation_repair_enabled
        if repair_enabled is None
        else repair_enabled
    )
    outcomes: list[PairQaOutcome] = []
    repaired_paths: list[str] = []
    lines: list[str] = []

    for ru_p, en_p in pairs:
        source_text = git_local.read_text(workdir, ru_p) or ""
        translated_text = git_local.read_text(workdir, en_p) or ""
        ru_diff, _ = pair_diffs.get((ru_p, en_p), (None, None))
        en_on_main: str | None = None
        if base_ref_local:
            en_on_main = git_local.read_text_at_ref(workdir, base_ref_local, en_p)

        initial_en = translated_text
        try:
            final_en, outcome = run_pair_qa(
                settings,
                ru_path=ru_p,
                en_path=en_p,
                source_text=source_text,
                translated_text=translated_text,
                source_lang="Russian",
                target_lang="English",
                source_pr_number=source_pr_number,
                ru_pr_diff=ru_diff,
                en_on_main=en_on_main,
                repair_enabled=repair_on,
            )
        except Exception as exc:
            err = str(exc).strip()
            if len(err) > 1200:
                err = err[:1200] + "…"
            final_en = translated_text
            outcome = PairQaOutcome(
                ru_path=ru_p,
                en_path=en_p,
                target_path=en_p,
                review_md=(
                    "### Вердикт\n**ПРИНИМАТЬ С ОГОВОРКАМИ**\n\n"
                    "### Блокеры\n_Нет._\n\n"
                    "### Оговорки\n"
                    f"- Ошибка QA pipeline: `{err}`.\n\n"
                    "### Кратко\n"
                    "QA pipeline упал; перевод оставлен как есть."
                ),
                repair_attempted=False,
                repair_applied=False,
                repair_skip_reason="api_error",
                confirmation_md=None,
                repair_error=err,
            )
        if final_en != initial_en:
            git_local.write_text(workdir, en_p, final_en)
            repaired_paths.append(en_p)
        outcomes.append(outcome)
        lines.append(format_pair_qa_markdown(outcome))

    summary = format_translation_pr_summary(
        source_pr_number=source_pr_number,
        outcomes=outcomes,
    )
    body = summary + "\n\n---\n\n" + "\n\n".join(lines) if lines else summary
    return body, repaired_paths, outcomes
