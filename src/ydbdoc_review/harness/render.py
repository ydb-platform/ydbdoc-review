"""Render and finalize helpers shared by harness steps."""

from __future__ import annotations

import copy
import logging

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.ast_types import Document
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import DEFAULT_PROMPT_VERSION
from ydbdoc_review.validation.fence_comments import (
    translate_cyrillic_fence_comments_with_client,
    translate_cyrillic_text_fences_with_client,
)
from ydbdoc_review.validation.fence_integrity import enforce_source_fenced_blocks
from ydbdoc_review.validation.glossary_toc_links import (
    en_mirror_path,
    strip_unreachable_internal_links,
)
from ydbdoc_review.validation.homoglyphs import postprocess_en_target_markdown
from ydbdoc_review.validation.link_locale import (
    localize_links_in_document,
    localize_links_in_text,
)
from ydbdoc_review.validation.prose_cyrillic import (
    translate_cyrillic_prose_with_client,
)

logger = logging.getLogger(__name__)


def render_with_translations(
    base_doc: Document,
    segments: list[Segment],
    translations: dict[str, str],
    *,
    target_lang: str = "en",
) -> str:
    doc = copy.deepcopy(base_doc)
    reinsert_segments(doc, segments, translations)
    localize_links_in_document(
        doc,
        target_lang=target_lang,
        source_doc=base_doc,
    )
    return render_markdown(doc, target_lang=target_lang)


def remap_translations_by_position(
    source_segments: list[Segment],
    target_segments: list[Segment],
    translations: dict[str, str],
) -> dict[str, str]:
    """Re-key translations from source-segment ids to target-segment ids."""
    return {
        tgt.id: translations[src.id]
        for src, tgt in zip(source_segments, target_segments, strict=True)
        if src.id in translations
    }


def finalize_en_target(
    text: str,
    normalized_source_text: str,
    *,
    client: YandexLLMClient | None = None,
    glossary: Glossary | None = None,
    file_path: str = "",
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    out_warnings: list[str] | None = None,
    en_toc_reachable: frozenset[str] | None = None,
) -> str:
    """Copy fenced bodies from reference, translate residual Cyrillic, postprocess."""
    text = enforce_source_fenced_blocks(text, normalized_source_text)
    if client is not None and glossary is not None:
        text = translate_cyrillic_fence_comments_with_client(
            text,
            client,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
            out_warnings=out_warnings,
        )
        text = translate_cyrillic_text_fences_with_client(
            text,
            client,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
            out_warnings=out_warnings,
        )
        text = translate_cyrillic_prose_with_client(
            text,
            client,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
            out_warnings=out_warnings,
        )
    text = localize_links_in_text(text, target_lang="en")
    if (
        en_toc_reachable is not None
        and target_lang.lower() in {"en", "english"}
    ):
        stripped: list[str] = []
        try:
            text = strip_unreachable_internal_links(
                text,
                file_path=en_mirror_path(file_path),
                reachable=en_toc_reachable,
                target_lang=target_lang,
                out_stripped=stripped,
            )
        except Exception as exc:  # noqa: BLE001 — never abort translate on strip
            logger.warning(
                "strip_unreachable_links failed for %s: %s",
                file_path or "(unknown)",
                exc,
            )
            if out_warnings is not None:
                out_warnings.append(
                    f"strip_unreachable_links_failed: {type(exc).__name__}: {exc}"
                )
        else:
            if stripped and out_warnings is not None:
                out_warnings.append(
                    f"strip_unreachable_links: removed {len(stripped)} internal "
                    f"href(s) outside EN toc graph"
                )
    return postprocess_en_target_markdown(text)
