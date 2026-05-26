"""Translate ``{% list tabs %}`` blocks without altering fenced code."""

from __future__ import annotations

from ydbdoc_review.config import Settings
from ydbdoc_review.document_segments import _is_fence_toggle, _read_fence
from ydbdoc_review.tabs_repair import is_tab_label_line


def _translate_prose_blob(
    settings: Settings,
    *,
    blob: str,
    source_path: str,
    source_lang: str,
    target_lang: str,
    label: str,
) -> str:
    from ydbdoc_review.llm import (
        _strip_code_fence,
        call_yandex_responses,
        clamp_max_output_tokens,
        load_translate_segment_instructions,
    )
    from ydbdoc_review.translate_postprocess import fix_yandex_cloud_links_for_en

    if not blob.strip():
        return blob
    from ydbdoc_review.prompt_builder import PromptBuilder

    instructions = load_translate_segment_instructions(
        settings,
        source_lang=source_lang,
        target_lang=target_lang,
    ).strip()
    user_input = PromptBuilder.build_segment_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        fragment_type="tabs",
        fragment_label=label,
        body=blob,
    )
    model = settings.model_translate
    cap = clamp_max_output_tokens(max(4096, min(len(blob) * 3, 32_768)), model)
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=cap,
            operation="translate:tabs-prose",
            detail=label,
        ).strip()
    )
    if target_lang.strip().lower() in ("english", "en"):
        out = fix_yandex_cloud_links_for_en(out)
    return out


def translate_tabs_block(
    settings: Settings,
    text: str,
    *,
    source_path: str,
    source_lang: str,
    target_lang: str,
    label: str,
) -> str:
    """Walk a list-tabs block; copy fences verbatim, translate other lines."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    prose_buf: list[str] = []
    part = 0

    def flush_prose() -> None:
        nonlocal part
        if not prose_buf:
            return
        blob = "\n".join(prose_buf)
        prose_buf.clear()
        part += 1
        out.append(
            _translate_prose_blob(
                settings,
                blob=blob,
                source_path=source_path,
                source_lang=source_lang,
                target_lang=target_lang,
                label=f"{label}/part-{part}",
            )
        )

    while i < len(lines):
        line = lines[i]
        if _is_fence_toggle(line):
            flush_prose()
            block, i = _read_fence(lines, i)
            out.append(block)
            continue
        if is_tab_label_line(line):
            flush_prose()
            out.append(line.rstrip())
            i += 1
            continue
        prose_buf.append(line)
        i += 1
    flush_prose()
    return "\n".join(out)
