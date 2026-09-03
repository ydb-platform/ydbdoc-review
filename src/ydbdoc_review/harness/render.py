"""Render and finalize helpers shared by harness steps."""

from __future__ import annotations

import copy
import logging
import re
from pathlib import PurePosixPath

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
from ydbdoc_review.validation.fence_integrity import (
    enforce_source_fenced_blocks,
    fence_structure_is_round_trip_stable,
)
from ydbdoc_review.validation.glossary_toc_links import (
    en_mirror_path,
    strip_unreachable_internal_links,
)
from ydbdoc_review.validation.homoglyphs import postprocess_en_target_markdown
from ydbdoc_review.validation.href_parity import restore_md_link_hrefs
from ydbdoc_review.validation.link_locale import (
    localize_links_in_document,
    localize_links_in_text,
)
from ydbdoc_review.validation.link_contract import LinkContractResult
from ydbdoc_review.validation.markdown_layout import repair_generated_markdown_layout
from ydbdoc_review.validation.prose_cyrillic import (
    translate_cyrillic_prose_with_client,
)
from ydbdoc_review.validation.yfm_anchor import (
    JobAnchorDictionary,
    apply_job_anchors_to_document,
    build_heading_anchor_map,
)

logger = logging.getLogger(__name__)

_INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_SOURCE_CERTIFICATE_SUBJECT_NOTATION = "Имя=Значение,...@<domain>"
_TARGET_CERTIFICATE_SUBJECT_NOTATION = "Name=Value,...@<domain>"


def _restore_cyrillic_source_code_atoms(text: str, source_text: str) -> str:
    """Restore the unique certificate-subject notation protected by #50976."""
    source_atoms = [
        atom
        for atom in _INLINE_CODE.finditer(source_text)
        if atom.group(1) == _SOURCE_CERTIFICATE_SUBJECT_NOTATION
    ]
    target_atoms = [
        atom
        for atom in _INLINE_CODE.finditer(text)
        if atom.group(1) == _TARGET_CERTIFICATE_SUBJECT_NOTATION
    ]
    if len(source_atoms) != 1 or len(target_atoms) != 1:
        return text
    source = source_atoms[0]
    target = target_atoms[0]
    return text[: target.start()] + source.group(0) + text[target.end() :]


def render_with_translations(
    base_doc: Document,
    segments: list[Segment],
    translations: dict[str, str],
    *,
    target_lang: str = "en",
    job_anchor_dictionary: JobAnchorDictionary | None = None,
) -> LinkContractResult:
    doc = copy.deepcopy(base_doc)
    reinsert_segments(doc, segments, translations)
    anchor_map = None
    dictionary = None
    tgt = target_lang.strip().lower()
    if tgt in {"en", "english"}:
        dictionary = job_anchor_dictionary or JobAnchorDictionary()
        apply_job_anchors_to_document(doc, dictionary=dictionary, source_doc=base_doc)
        anchor_map = build_heading_anchor_map(base_doc, doc)
        anchor_map.update(dictionary.as_map())
    localize_links_in_document(
        doc,
        target_lang=target_lang,
        source_doc=base_doc,
        anchor_map=anchor_map,
        dictionary=dictionary,
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


def finalize_en_target_result(
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
    layout_source_text: str | None = None,
    protected_source_text: str | None = None,
    source_base_text: str | None = None,
    target_baseline_text: str | None = None,
) -> LinkContractResult:
    """Copy fenced bodies from reference, translate residual Cyrillic, postprocess."""
    if fence_structure_is_round_trip_stable(normalized_source_text, lang=source_lang):
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
    text = postprocess_en_target_markdown(text)
    text = repair_generated_markdown_layout(layout_source_text or normalized_source_text, text)
    protected = protected_source_text or normalized_source_text
    link_result = restore_md_link_hrefs(
        text,
        protected,
        source_ru_base=source_base_text,
        target_baseline=target_baseline_text,
    )
    text = link_result.text
    text = _restore_cyrillic_source_code_atoms(text, protected)
    if en_toc_reachable is not None and target_lang.lower() in {"en", "english"}:
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
                out_warnings.append(f"strip_unreachable_links_failed: {type(exc).__name__}: {exc}")
        else:
            if stripped and out_warnings is not None:
                names = ", ".join(
                    f"`{PurePosixPath(h.split('#', 1)[0]).name}`" for h in stripped[:8]
                )
                extra = f", … (+{len(stripped) - 8})" if len(stripped) > 8 else ""
                out_warnings.append(
                    f"strip_unreachable_links: removed {len(stripped)} internal "
                    f"href(s) outside EN toc graph: {names}{extra}"
                )
    return LinkContractResult(text, link_result.issues)


def finalize_en_target(*args, **kwargs) -> str:
    """Compatibility API for strict Markdown consumers expecting a real str."""
    return finalize_en_target_result(*args, **kwargs).text
