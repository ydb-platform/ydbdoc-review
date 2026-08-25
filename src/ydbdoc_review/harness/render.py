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
from ydbdoc_review.validation.markdown_layout import repair_generated_markdown_layout
from ydbdoc_review.validation.prose_cyrillic import (
    translate_cyrillic_prose_with_client,
)

logger = logging.getLogger(__name__)

_INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _restore_cyrillic_source_code_atoms(text: str, source_text: str) -> str:
    """Restore only uniquely identifiable structured source code atoms."""
    source_atoms = list(_INLINE_CODE.finditer(source_text))
    target_atoms = list(_INLINE_CODE.finditer(text))
    def signature(value: str) -> str:
        return "".join(ch for ch in value if not ch.isalnum() and not ch.isspace())

    source_by_signature: dict[str, list[re.Match[str]]] = {}
    target_by_signature: dict[str, list[re.Match[str]]] = {}
    for atom in source_atoms:
        sig = signature(atom.group(1))
        if sig and _CYRILLIC.search(atom.group(1)):
            source_by_signature.setdefault(sig, []).append(atom)
    for atom in target_atoms:
        sig = signature(atom.group(1))
        if sig:
            target_by_signature.setdefault(sig, []).append(atom)

    replacements: list[tuple[re.Match[str], str]] = []
    for sig, sources in source_by_signature.items():
        targets = target_by_signature.get(sig, [])
        if len(sources) == len(targets) == 1:
            replacements.append((targets[0], sources[0].group(0)))
    out = text
    for target, source_value in sorted(replacements, key=lambda item: item[0].start(), reverse=True):
        out = out[: target.start()] + source_value + out[target.end() :]
    return out


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
    layout_source_text: str | None = None,
) -> str:
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
    text = restore_md_link_hrefs(text, normalized_source_text)
    text = _restore_cyrillic_source_code_atoms(text, normalized_source_text)
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
    return text
