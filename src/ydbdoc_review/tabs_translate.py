"""Translate ``{% list tabs %}`` blocks without altering fenced code."""

from __future__ import annotations

from ydbdoc_review.config import Settings
from ydbdoc_review.document_segments import _is_fence_toggle, _read_fence


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
    instructions = load_translate_segment_instructions(settings).strip()
    user_input = (
        f"File: `{source_path}`\n"
        f"Fragment type: `tabs`\n"
        f"Fragment label: `{label}`\n"
        f"SOURCE language: {source_lang}\n"
        f"TARGET language: {target_lang}\n\n"
        f"--- SOURCE BEGIN ---\n{blob}\n--- SOURCE END ---\n"
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
        prose_buf.append(line)
        i += 1
    flush_prose()
    return "\n".join(out)
