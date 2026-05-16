"""
Strict documentation translation: PR diff in small batches, merge into target file.

Falls back to full-file translation only when the target file is missing or too stale.
"""

from __future__ import annotations

import os
from typing import Literal

from ydbdoc_review.config import Settings
from ydbdoc_review.diff_hunks import batch_unified_diff
from ydbdoc_review.llm import (
    translate_en_update_from_ru_diff,
    translate_markdown,
    translate_ru_update_from_en_diff,
)
from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
from ydbdoc_review.translate_postprocess import translation_quality_issues

Direction = Literal["ru_to_en", "en_to_ru"]


def _allow_full_file_fallback() -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATE_ALLOW_FULL_FALLBACK", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def target_file_too_stale(
    *,
    target_reference: str | None,
    target_at_base: str | None,
    source_diff: str,
) -> bool:
    """True when incremental merge into *target_reference* is unreliable."""
    if not target_reference or not target_reference.strip():
        return True
    if len(target_reference.strip()) < 80:
        return True
    if not source_diff.strip():
        return False
    if not target_at_base or not target_at_base.strip():
        # No EN/RU on merge base — cannot align hunks to a parallel file.
        return len(target_reference.strip()) < 200
    adds = sum(
        1
        for line in source_diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    if adds > 8 and len(target_reference) < int(len(target_at_base) * 0.82):
        return True
    if len(target_at_base) > 2000 and len(target_reference) < int(len(target_at_base) * 0.55):
        return True
    return False


def strict_translate_document(
    settings: Settings,
    *,
    direction: Direction,
    source_path: str,
    target_path: str,
    source_full: str,
    target_reference: str | None,
    source_diff: str,
    target_at_base: str | None = None,
    use_full_source_from_base: bool = False,
) -> tuple[str, str]:
    """
    Translate one documentation pair using the strict pipeline.

    Returns ``(markdown, mode_label)`` where *mode_label* describes the path taken.
    """
    if direction == "ru_to_en":
        return _strict_ru_to_en(
            settings,
            ru_path=source_path,
            ru_full=source_full,
            en_reference=target_reference,
            ru_diff=source_diff,
            en_at_base=target_at_base,
            use_full_ru_from_base=use_full_source_from_base,
        )
    return _strict_en_to_ru(
        settings,
        en_path=source_path,
        en_full=source_full,
        ru_reference=target_reference,
        en_diff=source_diff,
        ru_at_base=target_at_base,
    )


def _strict_ru_to_en(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_reference: str | None,
    ru_diff: str,
    en_at_base: str | None,
    use_full_ru_from_base: bool,
) -> tuple[str, str]:
    ru_source = ru_full
    stale = target_file_too_stale(
        target_reference=en_reference,
        target_at_base=en_at_base,
        source_diff=ru_diff,
    )

    if use_full_ru_from_base or stale or en_reference is None:
        mode = "full-file"
        if use_full_ru_from_base:
            mode = "full-file-from-base-ru"
        elif stale and en_reference is not None:
            mode = "full-file-stale-target"
        elif en_reference is None:
            mode = "full-file-no-target"
        out = translate_markdown(
            settings,
            source_lang="Russian",
            target_lang="English",
            source_path=ru_path,
            source_text=ru_source,
        )
        return restore_markdown_links_from_ru(ru_source, out), mode

    if not ru_diff.strip():
        return en_reference, "unchanged-no-diff"

    batches = batch_unified_diff(ru_diff)
    en_cur = en_reference
    if len(batches) == 1:
        try:
            en_cur = translate_en_update_from_ru_diff(
                settings,
                en_reference=en_reference,
                ru_diff=batches[0],
                ru_path=ru_path,
                ru_full=ru_source,
            )
        except Exception:
            if not _allow_full_file_fallback():
                raise
            out = translate_markdown(
                settings,
                source_lang="Russian",
                target_lang="English",
                source_path=ru_path,
                source_text=ru_source,
            )
            return restore_markdown_links_from_ru(ru_source, out), "full-file-after-diff-error"
        mode = "diff-incremental" if len(batches) > 1 else "diff"
    else:
        for i, batch in enumerate(batches, start=1):
            try:
                en_cur = translate_en_update_from_ru_diff(
                    settings,
                    en_reference=en_cur,
                    ru_diff=batch,
                    ru_path=ru_path,
                    ru_full=ru_source,
                )
            except Exception:
                if not _allow_full_file_fallback():
                    raise
                out = translate_markdown(
                    settings,
                    source_lang="Russian",
                    target_lang="English",
                    source_path=ru_path,
                    source_text=ru_source,
                )
                return (
                    restore_markdown_links_from_ru(ru_source, out),
                    f"full-file-after-diff-batch-{i}-error",
                )
        mode = f"diff-incremental-{len(batches)}-batches"

    out = restore_markdown_links_from_ru(ru_source, en_cur)
    issues = translation_quality_issues(ru_source, out, target_lang="English")
    if issues and _allow_full_file_fallback():
        out2 = translate_markdown(
            settings,
            source_lang="Russian",
            target_lang="English",
            source_path=ru_path,
            source_text=ru_source,
        )
        out2 = restore_markdown_links_from_ru(ru_source, out2)
        if not translation_quality_issues(ru_source, out2, target_lang="English"):
            return out2, f"{mode}-heuristic-fallback-full"
    return out, mode


def _strict_en_to_ru(
    settings: Settings,
    *,
    en_path: str,
    en_full: str,
    ru_reference: str | None,
    en_diff: str,
    ru_at_base: str | None,
) -> tuple[str, str]:
    stale = target_file_too_stale(
        target_reference=ru_reference,
        target_at_base=ru_at_base,
        source_diff=en_diff,
    )

    if stale or ru_reference is None:
        mode = "full-file-stale-target" if ru_reference else "full-file-no-target"
        out = translate_markdown(
            settings,
            source_lang="English",
            target_lang="Russian",
            source_path=en_path,
            source_text=en_full,
        )
        return out, mode

    if not en_diff.strip():
        return ru_reference, "unchanged-no-diff"

    batches = batch_unified_diff(en_diff)
    ru_cur = ru_reference
    if len(batches) == 1:
        try:
            ru_cur = translate_ru_update_from_en_diff(
                settings,
                ru_reference=ru_reference,
                en_diff=batches[0],
                en_path=en_path,
                en_full=en_full,
            )
        except Exception:
            if not _allow_full_file_fallback():
                raise
            out = translate_markdown(
                settings,
                source_lang="English",
                target_lang="Russian",
                source_path=en_path,
                source_text=en_full,
            )
            return out, "full-file-after-diff-error"
        mode = "diff"
    else:
        for i, batch in enumerate(batches, start=1):
            try:
                ru_cur = translate_ru_update_from_en_diff(
                    settings,
                    ru_reference=ru_cur,
                    en_diff=batch,
                    en_path=en_path,
                    en_full=en_full,
                )
            except Exception:
                if not _allow_full_file_fallback():
                    raise
                out = translate_markdown(
                    settings,
                    source_lang="English",
                    target_lang="Russian",
                    source_path=en_path,
                    source_text=en_full,
                )
                return out, f"full-file-after-diff-batch-{i}-error"
        mode = f"diff-incremental-{len(batches)}-batches"

    issues = translation_quality_issues(en_full, ru_cur, target_lang="Russian")
    if issues and _allow_full_file_fallback():
        out2 = translate_markdown(
            settings,
            source_lang="English",
            target_lang="Russian",
            source_path=en_path,
            source_text=en_full,
        )
        if not translation_quality_issues(en_full, out2, target_lang="Russian"):
            return out2, f"{mode}-heuristic-fallback-full"
    return ru_cur, mode
