"""Render and finalize helpers shared by harness steps."""

from __future__ import annotations

import copy
import logging
import re
from pathlib import PurePosixPath

from markdown_it.token import Token

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.ast_types import Document
from ydbdoc_review.parsing.markdown_parser import create_parser
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
from ydbdoc_review.validation.homoglyphs import (
    decode_percent_encoded_protect_markers,
    fix_cyrillic_homoglyphs_in_en,
    fix_russian_angle_placeholders_in_en,
)
from ydbdoc_review.validation.href_parity import (
    restore_md_link_hrefs,
    retarget_source_owned_redirect_hrefs,
)
from ydbdoc_review.validation.link_contract import LinkContractResult
from ydbdoc_review.validation.link_locale import (
    localize_links_in_document,
    localize_links_in_text,
)
from ydbdoc_review.validation.markdown_layout import (
    fix_blanks_around_fences,
    fix_image_bang_spacing,
    fix_no_space_in_emphasis,
    repair_generated_markdown_layout,
)
from ydbdoc_review.validation.prose_cyrillic import (
    translate_cyrillic_prose_with_client,
)
from ydbdoc_review.validation.yfm_anchor import (
    JobAnchorDictionary,
    apply_job_anchors_to_document,
    build_heading_anchor_map,
)

logger = logging.getLogger(__name__)

_SOURCE_CERTIFICATE_SUBJECT_NOTATION = "Имя=Значение,...@<domain>"
_TARGET_CERTIFICATE_SUBJECT_NOTATION = "Name=Value,...@<domain>"
_CERTIFICATE_INLINE_CODE = re.compile(
    rf"(?<!`)(?P<marker>`+)[ \r\n]*"
    rf"(?P<content>{re.escape(_SOURCE_CERTIFICATE_SUBJECT_NOTATION)})[ \r\n]*"
    rf"(?P=marker)(?!`)"
)


def _token_identity(token: Token) -> tuple:
    """Markdown-it token structure excluding source content and children."""
    return (
        token.type,
        token.tag,
        token.nesting,
        token.level,
        token.markup,
        token.info,
        token.attrs,
        token.meta,
        token.block,
        token.hidden,
        token.map,
    )


def _is_complete_inline_code_replacement(
    before_text: str,
    after_text: str,
) -> bool:
    """Return whether one source edit changes exactly one parsed InlineCode atom."""
    try:
        before_tokens = create_parser().parse(before_text)
        after_tokens = create_parser().parse(after_text)
    except Exception:
        return False
    if len(before_tokens) != len(after_tokens):
        return False

    changed_inline_atoms = 0
    for before, after in zip(before_tokens, after_tokens, strict=True):
        if _token_identity(before) != _token_identity(after):
            return False
        if before.type != "inline":
            if before.content != after.content:
                return False
            continue

        before_children = before.children or []
        after_children = after.children or []
        if len(before_children) != len(after_children):
            return False
        for before_child, after_child in zip(before_children, after_children, strict=True):
            if _token_identity(before_child) != _token_identity(after_child):
                return False
            if before_child.content == after_child.content:
                continue
            if (
                before_child.type != "code_inline"
                or before_child.content != _SOURCE_CERTIFICATE_SUBJECT_NOTATION
                or after_child.content != _TARGET_CERTIFICATE_SUBJECT_NOTATION
            ):
                return False
            changed_inline_atoms += 1
    return changed_inline_atoms == 1


def _localize_certificate_subject_notation(text: str) -> str:
    """Localize only complete parsed certificate Subject inline-code atoms."""
    replacements: list[tuple[int, int]] = []
    for atom in _CERTIFICATE_INLINE_CODE.finditer(text):
        candidate = (
            text[: atom.start("content")]
            + _TARGET_CERTIFICATE_SUBJECT_NOTATION
            + text[atom.end("content") :]
        )
        if _is_complete_inline_code_replacement(text, candidate):
            replacements.append((atom.start("content"), atom.end("content")))

    for start, end in reversed(replacements):
        text = text[:start] + _TARGET_CERTIFICATE_SUBJECT_NOTATION + text[end:]
    return text


def _postprocess_en_target_without_inline_notation(text: str) -> str:
    """Apply the legacy EN postprocessors except their fuzzy notation rewrite."""
    text = fix_cyrillic_homoglyphs_in_en(text)
    text = fix_russian_angle_placeholders_in_en(text)
    text = decode_percent_encoded_protect_markers(text)
    text = fix_image_bang_spacing(text)
    text = fix_no_space_in_emphasis(text)
    return fix_blanks_around_fences(text)


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
    text: str | LinkContractResult,
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
    docs_text_reader=None,
) -> LinkContractResult:
    """Copy fenced bodies from reference, translate residual Cyrillic, postprocess."""
    incoming_issues = ()
    if isinstance(text, LinkContractResult):
        incoming_issues = text.issues
        text = text.text
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
    text = _postprocess_en_target_without_inline_notation(text)
    text = repair_generated_markdown_layout(layout_source_text or normalized_source_text, text)
    protected = protected_source_text or normalized_source_text
    link_result = restore_md_link_hrefs(
        text,
        protected,
        source_ru_base=source_base_text,
        target_baseline=target_baseline_text,
    )
    text = link_result.text
    text = _localize_certificate_subject_notation(text)
    if (
        docs_text_reader is not None
        and source_lang.lower() in {"ru", "russian"}
        and target_lang.lower() in {"en", "english"}
        and file_path
    ):
        text = retarget_source_owned_redirect_hrefs(
            text,
            protected,
            en_page_path=en_mirror_path(file_path),
            read_text=docs_text_reader,
        )
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
    return LinkContractResult(text, incoming_issues + link_result.issues)


def finalize_en_target(*args, **kwargs) -> str:
    """Compatibility API for strict Markdown consumers expecting a real str."""
    return finalize_en_target_result(*args, **kwargs).text
