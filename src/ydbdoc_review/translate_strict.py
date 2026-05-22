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
    _diff_en_update_looks_truncated,
    translate_en_update_from_ru_diff,
    translate_markdown,
    translate_ru_update_from_en_diff,
)
from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
from ydbdoc_review.section_translate import (
    translate_by_section_enabled,
    translate_en_to_ru_by_sections,
    translate_full_source_by_sections,
    translate_ru_to_en_by_sections,
)
from ydbdoc_review.ru_en_alignment import (
    critical_ru_en_mismatches,
    en_coverage_behind_ru,
    ru_authority_resync_enabled,
    ru_authority_text,
)
from ydbdoc_review.translate_postprocess import (
    apply_post_translation_fixes,
    translation_quality_issues,
)

Direction = Literal["ru_to_en", "en_to_ru"]


def _allow_full_file_fallback() -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATE_ALLOW_FULL_FALLBACK", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def prefer_diff_only_translation(
    source_diff: str,
    source_full: str,
    *,
    en_reference: str | None = None,
) -> bool:
    """
    Small PR diff on a long file → patch EN via RU_DIFF only (no section copy from main).

    Avoids mixing stale EN chunks with a few translated lines (the #40070 failure mode).
    """
    raw = os.environ.get("YDBDOC_TRANSLATE_DIFF_FIRST", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    diff = source_diff.strip()
    if not diff or len(source_full) < 1000:
        return False
    max_chars = 6000
    max_raw = os.environ.get("YDBDOC_TRANSLATE_DIFF_FIRST_MAX_CHARS", "").strip()
    if max_raw.isdigit():
        max_chars = max(500, int(max_raw))
    if len(diff) > max_chars:
        return False
    from ydbdoc_review.ru_en_structure import (
        index_bullets_behind_ru,
        list_tab_item_labels,
    )

    if list_tab_item_labels(source_full) and len(
        list_tab_item_labels(source_full)
    ) > len(list_tab_item_labels(en_reference or "")):
        return False
    if index_bullets_behind_ru(source_full, en_reference or ""):
        return False
    max_ratio = 0.12
    ratio_raw = os.environ.get("YDBDOC_TRANSLATE_DIFF_FIRST_MAX_RATIO", "").strip()
    if ratio_raw:
        try:
            max_ratio = float(ratio_raw)
        except ValueError:
            pass
    return len(diff) < int(len(source_full) * max_ratio)


def _translate_ru_to_en_via_diff(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_reference: str,
    ru_diff: str,
) -> tuple[str, str]:
    """Apply RU unified diff to EN reference in one or more batches."""
    batches = batch_unified_diff(ru_diff)
    en_cur = en_reference
    for i, batch in enumerate(batches, start=1):
        try:
            en_cur = translate_en_update_from_ru_diff(
                settings,
                en_reference=en_cur,
                ru_diff=batch,
                ru_path=ru_path,
                ru_full=ru_full,
            )
        except Exception:
            if not _allow_full_file_fallback():
                raise
            out = translate_markdown(
                settings,
                source_lang="Russian",
                target_lang="English",
                source_path=ru_path,
                source_text=ru_full,
            )
            return restore_markdown_links_from_ru(ru_full, out), "full-file-after-diff-error"
    mode = "diff" if len(batches) == 1 else f"diff-incremental-{len(batches)}-batches"
    out = restore_markdown_links_from_ru(ru_full, en_cur)
    return out, mode


def _full_resync_from_ru(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
) -> tuple[str, str]:
    """Replace EN with full translation from RU (source PR is authority)."""
    if translate_by_section_enabled(source_len=len(ru_full)):
        out, sec_mode = translate_full_source_by_sections(
            settings,
            source_path=ru_path,
            source_full=ru_full,
            source_lang="Russian",
            target_lang="English",
        )
        out = restore_markdown_links_from_ru(ru_full, out)
        from ydbdoc_review.ru_en_sync import finalize_en_from_ru

        return finalize_en_from_ru(
            settings, ru_path=ru_path, ru_full=ru_full, en_text=out
        ), sec_mode
    out = translate_markdown(
        settings,
        source_lang="Russian",
        target_lang="English",
        source_path=ru_path,
        source_text=ru_full,
    )
    out = restore_markdown_links_from_ru(ru_full, out)
    from ydbdoc_review.ru_en_sync import finalize_en_from_ru

    return finalize_en_from_ru(
        settings, ru_path=ru_path, ru_full=ru_full, en_text=out
    ), "full-file"


def _apply_ru_authority_if_needed(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_reference: str | None,
    out: str,
    mode: str,
    ru_at_base: str | None = None,
) -> tuple[str, str]:
    if not ru_authority_resync_enabled() or not en_reference:
        return out, mode
    reasons = critical_ru_en_mismatches(
        ru_full, out, en_reference=en_reference, ru_authority=ru_at_base
    )
    if not reasons:
        return out, mode
    ru_resync = ru_authority_text(ru_full, ru_at_base)
    resynced, resync_mode = _full_resync_from_ru(
        settings, ru_path=ru_path, ru_full=ru_resync
    )
    tag = ",".join(reasons[:4])
    return resynced, f"ru-authority-{resync_mode}-after-{mode}-{tag}"


def target_file_too_stale(
    *,
    target_reference: str | None,
    target_at_base: str | None,
    source_diff: str,
    source_full: str | None = None,
    source_at_base: str | None = None,
) -> bool:
    """True when incremental merge into *target_reference* is unreliable."""
    if not target_reference or not target_reference.strip():
        return True
    if len(target_reference.strip()) < 80:
        return True
    ru_auth = (
        ru_authority_text(source_full, source_at_base)
        if source_full
        else (source_at_base or "")
    )
    if ru_auth and target_reference and en_coverage_behind_ru(ru_auth, target_reference):
        return True
    if ru_auth and target_at_base and en_coverage_behind_ru(ru_auth, target_at_base):
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
    source_at_base: str | None = None,
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
            ru_at_base=source_at_base,
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
    ru_at_base: str | None = None,
    use_full_ru_from_base: bool,
) -> tuple[str, str]:
    ru_source = ru_full
    ru_work = ru_authority_text(ru_full, ru_at_base)
    stale = target_file_too_stale(
        target_reference=en_reference,
        target_at_base=en_at_base,
        source_diff=ru_diff,
        source_full=ru_source,
        source_at_base=ru_at_base,
    )

    if use_full_ru_from_base or stale or en_reference is None:
        if use_full_ru_from_base:
            mode = "full-file-from-base-ru"
        elif stale and en_reference is not None:
            mode = "full-file-stale-target"
        else:
            mode = "full-file-no-target"
        behind = en_reference is not None and en_coverage_behind_ru(
            ru_work, en_reference
        )
        if translate_by_section_enabled(source_len=len(ru_work)) and not behind:
            out, mode_sec = translate_full_source_by_sections(
                settings,
                source_path=ru_path,
                source_full=ru_work,
                source_lang="Russian",
                target_lang="English",
            )
            out = restore_markdown_links_from_ru(ru_work, out)
            from ydbdoc_review.ru_en_sync import finalize_en_from_ru

            out = finalize_en_from_ru(
                settings,
                ru_path=ru_path,
                ru_full=ru_work,
                en_text=out,
            )
            return _apply_ru_authority_if_needed(
                settings,
                ru_path=ru_path,
                ru_full=ru_source,
                en_reference=en_reference,
                out=out,
                mode=f"{mode}-{mode_sec}",
                ru_at_base=ru_at_base,
            )
        out = translate_markdown(
            settings,
            source_lang="Russian",
            target_lang="English",
            source_path=ru_path,
            source_text=ru_work,
        )
        out = restore_markdown_links_from_ru(ru_work, out)
        from ydbdoc_review.ru_en_sync import finalize_en_from_ru

        out = finalize_en_from_ru(
            settings, ru_path=ru_path, ru_full=ru_work, en_text=out
        )
        return _apply_ru_authority_if_needed(
            settings,
            ru_path=ru_path,
            ru_full=ru_source,
            en_reference=en_reference,
            out=out,
            mode=mode,
            ru_at_base=ru_at_base,
        )

    if not ru_diff.strip():
        return en_reference, "unchanged-no-diff"

    if prefer_diff_only_translation(ru_diff, ru_source, en_reference=en_reference):
        out, mode = _translate_ru_to_en_via_diff(
            settings,
            ru_path=ru_path,
            ru_full=ru_source,
            en_reference=en_reference,
            ru_diff=ru_diff,
        )
        return _apply_ru_authority_if_needed(
            settings,
            ru_path=ru_path,
            ru_full=ru_source,
            en_reference=en_reference,
            out=out,
            mode=f"diff-first-{mode}",
            ru_at_base=ru_at_base,
        )

    if translate_by_section_enabled(source_len=len(ru_source)):
        out, mode = translate_ru_to_en_by_sections(
            settings,
            ru_path=ru_path,
            ru_full=ru_source,
            en_reference=en_reference,
            ru_diff=ru_diff,
        )
        return _apply_ru_authority_if_needed(
            settings,
            ru_path=ru_path,
            ru_full=ru_source,
            en_reference=en_reference,
            out=out,
            mode=mode,
            ru_at_base=ru_at_base,
        )

    out, mode = _translate_ru_to_en_via_diff(
        settings,
        ru_path=ru_path,
        ru_full=ru_source,
        en_reference=en_reference,
        ru_diff=ru_diff,
    )
    return _apply_ru_authority_if_needed(
        settings,
        ru_path=ru_path,
        ru_full=ru_source,
        en_reference=en_reference,
        out=out,
        mode=mode,
        ru_at_base=ru_at_base,
    )


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
        if translate_by_section_enabled(source_len=len(en_full)):
            out, mode_sec = translate_full_source_by_sections(
                settings,
                source_path=en_path,
                source_full=en_full,
                source_lang="English",
                target_lang="Russian",
            )
            return out, f"{mode}-{mode_sec}"
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

    if translate_by_section_enabled(source_len=len(en_full)):
        return translate_en_to_ru_by_sections(
            settings,
            en_path=en_path,
            en_full=en_full,
            ru_reference=ru_reference,
            en_diff=en_diff,
        )

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
