from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from ydbdoc_review.config import Settings, fm_base_url_requires_yandex_folder
from ydbdoc_review.markdown_blocks import (
    prose_mask_enabled,
    translate_preserving_blocks,
)
from ydbdoc_review.markdown_chunk import (
    split_markdown_for_translate,
    translate_chunk_target_chars,
)
from ydbdoc_review.translate_postprocess import (
    fix_yandex_cloud_links_for_en,
    should_retry_chunk,
    translation_quality_issues,
)


def _model_uri(settings: Settings, model: str) -> str:
    m = model.strip()
    if m.startswith("gpt://"):
        return m
    if fm_base_url_requires_yandex_folder(settings.yandex_base_url):
        if not settings.yandex_folder:
            raise ValueError("Yandex FM requires a folder id when the model id is not gpt://…")
        return f"gpt://{settings.yandex_folder}/{m}"
    return m


def _read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def parse_json_object(text: str) -> dict:
    t = _strip_code_fence(text)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", t)
        if not m:
            raise
        return json.loads(m.group(0))


def _format_response_api_error(err: Any) -> str:
    code = getattr(err, "code", None)
    msg = getattr(err, "message", None)
    parts = [p for p in (code, msg) if p]
    return " — ".join(str(p) for p in parts) if parts else repr(err)


def _text_from_response_output_dump(resp: Any) -> str:
    """Collect assistant text when provider uses non-standard content block types."""
    dump_fn = getattr(resp, "model_dump", None)
    if not callable(dump_fn):
        return ""
    try:
        d: dict[str, Any] = dump_fn(mode="python")
    except Exception:
        return ""
    texts: list[str] = []
    for item in d.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            t = block.get("text")
            if isinstance(t, str) and t.strip():
                texts.append(t)
    return "".join(texts)


def _responses_error_message(resp: Any) -> str | None:
    err = getattr(resp, "error", None)
    if err is None:
        return None
    return _format_response_api_error(err)


def _extract_responses_text(resp: Any) -> str:
    """Return assistant text only; do not raise on FM `error` field (try chat fallback)."""
    primary = getattr(resp, "output_text", None)
    if isinstance(primary, str) and primary.strip():
        return primary
    loose = _text_from_response_output_dump(resp)
    if loose.strip():
        return loose
    return ""


def _call_fm_chat_completions(
    client: OpenAI,
    *,
    model_uri: str,
    instructions: str,
    user_input: str,
    max_output_tokens: int,
) -> str:
    # Yandex FM examples use max_tokens; some gateways reject max_completion_tokens.
    comp = client.chat.completions.create(
        model=model_uri,
        temperature=0.2,
        max_tokens=max_output_tokens,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ],
    )
    choice = comp.choices[0]
    msg = choice.message
    content = msg.content
    if isinstance(content, str) and content.strip():
        return content
    raise RuntimeError(
        "Chat completions returned no text in choices[0].message.content "
        f"(finish_reason={getattr(choice, 'finish_reason', None)!r})."
    )


def call_yandex_responses(
    settings: Settings,
    model: str,
    instructions: str,
    user_input: str,
    max_output_tokens: int,
) -> str:
    max_output_tokens = clamp_max_output_tokens(max_output_tokens, model)
    settings.validate_yandex()
    # Folder is already in `gpt://<folder>/<model>`; `OpenAI-Project` duplicates it
    # and some Yandex FM deployments return "Failed to get model" when both are set.
    client = OpenAI(
        api_key=settings.yandex_api_key,
        base_url=settings.yandex_base_url,
    )
    model_uri = _model_uri(settings, model)
    responses_err: str | None = None
    skip_resp = os.environ.get("YDBDOC_FM_SKIP_RESPONSES_API", "").strip() in (
        "1",
        "true",
        "yes",
    )
    if not skip_resp:
        resp = client.responses.create(
            model=model_uri,
            temperature=0.2,
            instructions=instructions,
            input=user_input,
            max_output_tokens=max_output_tokens,
        )
        responses_err = _responses_error_message(resp)
        out = _extract_responses_text(resp)
        if out.strip():
            return out
    else:
        resp = None  # type: ignore[assignment]

    # Chat completions is the path most Yandex OpenAI examples use; also covers
    # empty output_text and responses body errors (e.g. model not on Responses route).
    try:
        out = _call_fm_chat_completions(
            client,
            model_uri=model_uri,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=max_output_tokens,
        )
    except Exception as chat_exc:
        status = getattr(resp, "status", None) if resp is not None else None
        inc = getattr(resp, "incomplete_details", None) if resp is not None else None
        snippet = ""
        if resp is not None:
            dump_fn = getattr(resp, "model_dump_json", None)
            if callable(dump_fn):
                try:
                    snippet = f"\nresponses.create body (truncated): {dump_fn()[:4000]}"
                except Exception:
                    pass
        parts = [f"chat.completions failed: {chat_exc!s}"]
        if responses_err:
            parts.insert(0, f"responses API: {responses_err}")
        raise RuntimeError(
            "Foundation Models call failed.\n"
            + "\n".join(parts)
            + f"\n(status={status!r}, incomplete_details={inc!r}).{snippet}"
        ) from chat_exc
    if out.strip():
        return out

    status = getattr(resp, "status", None) if resp is not None else None
    inc = getattr(resp, "incomplete_details", None) if resp is not None else None
    snippet = ""
    if resp is not None:
        dump_fn = getattr(resp, "model_dump_json", None)
        if callable(dump_fn):
            try:
                raw_json = dump_fn()[:4000]
                snippet = f"\nresponses.create body (truncated): {raw_json}"
            except Exception:
                pass
    parts: list[str] = []
    if responses_err:
        parts.append(f"responses API: {responses_err}")
    parts.append("chat.completions returned empty assistant content.")
    raise RuntimeError(
        "\n".join(parts)
        + f"\n(status={status!r}, incomplete_details={inc!r}).{snippet}"
    )


def load_analyze_instructions(settings: Settings) -> str:
    p = Path(settings.prompts_dir) / "01_analyze_translation_pairs.txt"
    return _read_prompt(p)


def load_translate_instructions(settings: Settings) -> str:
    p = Path(settings.prompts_dir) / "02_translate_article.txt"
    return _read_prompt(p)


# Upper bound for max_output_tokens we pass to the API (typo guard). Provider may
# clamp lower. Set ``YDBDOC_TRANSLATE_MAX_OUTPUT_TOKENS=0`` for this ceiling.
_TRANSLATE_OUTPUT_HARD_CEILING = 1_048_576

# Yandex FM rejects higher values for some models (e.g. DeepSeek with reasoning).
_KNOWN_MODEL_COMPLETION_CEILINGS: tuple[tuple[str, int], ...] = (
    ("deepseek", 32_768),
)


def _model_completion_token_ceiling(model: str) -> int:
    """Provider-specific max completion tokens (slug substring match)."""
    raw = os.environ.get("YDBDOC_MODEL_COMPLETION_TOKEN_CEILING", "").strip()
    if raw.isdigit():
        return max(1024, int(raw))
    slug = model.lower()
    for needle, ceiling in _KNOWN_MODEL_COMPLETION_CEILINGS:
        if needle in slug:
            return ceiling
    return _TRANSLATE_OUTPUT_HARD_CEILING


def clamp_max_output_tokens(requested: int, model: str) -> int:
    ceiling = _model_completion_token_ceiling(model)
    return max(1024, min(requested, ceiling))


def _translate_user_payload(
    settings: Settings,
    *,
    instructions: str,
    user_input: str,
    reference_for_truncation: str,
    model: str,
    max_output_tokens: int | None = None,
) -> str:
    """Single FM call + optional retry when the completion looks truncated."""
    cap = (
        clamp_max_output_tokens(max_output_tokens, model)
        if max_output_tokens is not None
        else _translate_max_output_tokens(model)
    )
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=cap,
        ).strip()
    )
    if _full_file_translation_looks_truncated(out, reference_for_truncation):
        cap2 = _translate_retry_max_tokens(cap, model)
        if cap2 > cap:
            out = _strip_code_fence(
                call_yandex_responses(
                    settings,
                    model,
                    instructions=instructions.strip(),
                    user_input=user_input,
                    max_output_tokens=cap2,
                ).strip()
            )
    return out


def _translate_markdown_user_input(
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    source_text: str,
    chunk_preamble: str = "",
) -> str:
    body = (
        f"Source language: {source_lang}\n"
        f"Target language: {target_lang}\n"
        f"Source file path: {source_path}\n\n"
        f"--- SOURCE BEGIN ---\n{source_text}\n--- SOURCE END ---\n\n"
        "Output only the translated markdown."
    )
    return f"{chunk_preamble}{body}" if chunk_preamble else body


def _chunk_translate_max_tokens(chunk_len: int, model: str) -> int:
    """Completion budget for one fragment (UTF-8 length → rough token budget)."""
    est = max(12_288, int(chunk_len * 2.3))
    return clamp_max_output_tokens(est, model)


def _is_english_target(target_lang: str) -> bool:
    return target_lang.strip().lower() in ("english", "en")


def _postprocess_target_language(text: str, *, target_lang: str) -> str:
    if _is_english_target(target_lang):
        return fix_yandex_cloud_links_for_en(text)
    return text


def _translate_chunk_with_retry(
    settings: Settings,
    *,
    instructions: str,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk: str,
    chunk_index: int,
    chunk_count: int,
    default_cap: int,
) -> str:
    preamble = (
        f"This is fragment {chunk_index} of {chunk_count} of a single markdown file "
        f"(`{source_path}`).\n"
        "Translate only this fragment. Reply with the translated markdown for this "
        "fragment only — no preamble, part labels, or commentary.\n"
        "Keep Diplodoc directives unchanged: `{% list tabs %}`, `{% endlist %}`, "
        "`{% note %}`, `{{ ydb-short-name }}`, etc.\n"
        "If the fragment starts or ends inside a code fence, keep the same fence "
        "markers (` ``` ` / ` ```yaml ` etc.) so the merged file has valid fences.\n"
        "Do not invent CLI flags or rename token/output filenames; mirror the source.\n"
        "Do not omit sections or summarize — translate every line in the fragment.\n\n"
    )
    user_input = _translate_markdown_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        source_text=chunk,
        chunk_preamble=preamble,
    )
    model = settings.model_translate
    cap = min(default_cap, _chunk_translate_max_tokens(len(chunk), model))
    out = _translate_user_payload(
        settings,
        instructions=instructions,
        user_input=user_input,
        reference_for_truncation=chunk,
        model=model,
        max_output_tokens=cap,
    )
    if should_retry_chunk(chunk, out):
        cap2 = min(_translate_retry_max_tokens(cap, model), default_cap)
        if cap2 > cap:
            out2 = _translate_user_payload(
                settings,
                instructions=instructions,
                user_input=user_input,
                reference_for_truncation=chunk,
                model=model,
                max_output_tokens=cap2,
            )
            if len(out2) > len(out):
                out = out2
    return out


def _allow_full_document_retry() -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATE_ALLOW_FULL_RETRY", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _allow_diff_to_full_fallback() -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATE_ALLOW_FULL_FALLBACK", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _maybe_retry_full_document(
    settings: Settings,
    *,
    instructions: str,
    source_lang: str,
    target_lang: str,
    source_path: str,
    source_text: str,
    merged: str,
    default_cap: int,
) -> str:
    """Re-translate the whole file once when chunked output looks incomplete."""
    if not _allow_full_document_retry():
        return merged
    issues = translation_quality_issues(
        source_text, merged, target_lang=target_lang
    )
    retry_codes = {
        "too_short",
        "missing_tabs",
        "unbalanced_fences",
        "cyrillic_leak",
        "en_behind_ru",
    }
    if not retry_codes.intersection(issues):
        return merged
    user_input = _translate_markdown_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        source_text=source_text,
    )
    model = settings.model_translate
    cap = default_cap
    retry_out = _translate_user_payload(
        settings,
        instructions=instructions,
        user_input=user_input,
        reference_for_truncation=source_text,
        model=model,
        max_output_tokens=cap,
    )
    retry_out = _postprocess_target_language(retry_out, target_lang=target_lang)
    if len(retry_out) > int(len(merged) * 1.05):
        return retry_out
    if translation_quality_issues(source_text, retry_out, target_lang=target_lang):
        if len(retry_out) >= len(merged):
            return retry_out
    return merged


def _ru_to_en_preserve_blocks(source_lang: str, target_lang: str) -> bool:
    return source_lang.lower().startswith("rus") and target_lang.strip().lower() in (
        "english",
        "en",
    )


def _translate_preserving_blocks(
    settings: Settings,
    *,
    instructions: str,
    source_lang: str,
    target_lang: str,
    source_path: str,
    source_text: str,
    model: str,
    default_cap: int,
) -> str:
    """Translate prose segments only; fences and Liquid tags stay verbatim."""

    def translate_prose(prose: str) -> str:
        if len(prose) <= translate_chunk_target_chars():
            user_input = _translate_markdown_user_input(
                source_lang=source_lang,
                target_lang=target_lang,
                source_path=source_path,
                source_text=prose,
            )
            out = _translate_user_payload(
                settings,
                instructions=instructions,
                user_input=user_input,
                reference_for_truncation=prose,
                model=model,
                max_output_tokens=default_cap,
            )
            return _postprocess_target_language(out, target_lang=target_lang)
        chunks = split_markdown_for_translate(prose, target_chars=translate_chunk_target_chars())
        parts: list[str] = []
        for i, ch in enumerate(chunks, start=1):
            parts.append(
                _translate_chunk_with_retry(
                    settings,
                    instructions=instructions,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source_path=source_path,
                    chunk=ch,
                    chunk_index=i,
                    chunk_count=len(chunks),
                    default_cap=default_cap,
                )
            )
        return _postprocess_target_language("\n".join(parts), target_lang=target_lang)

    def translate_comment(comment: str) -> str:
        if not (
            source_lang.lower().startswith("rus")
            and target_lang.strip().lower() in ("english", "en")
        ):
            return comment
        return translate_comment_line_ru_to_en(
            settings, ru_path=source_path, comment=comment
        )

    return translate_preserving_blocks(
        source_text, translate_prose, translate_comment
    )


def translate_comment_line_ru_to_en(
    settings: Settings,
    *,
    ru_path: str,
    comment: str,
) -> str:
    """Translate one ``--`` or ``#`` comment line inside a code fence."""
    text = comment.strip()
    if not text:
        return comment
    instructions = load_translate_instructions(settings)
    user_input = (
        f"File: `{ru_path}`\n"
        "Translate this **single comment line** from Russian to English.\n"
        "Output only the translated comment text (no markdown fence, no quotes).\n"
        "Keep technical identifiers (table/column names) unchanged.\n\n"
        f"{text}"
    )
    out = _translate_user_payload(
        settings,
        instructions=instructions,
        user_input=user_input,
        reference_for_truncation=text,
        model=settings.model_translate,
        max_output_tokens=min(512, _translate_max_output_tokens(settings.model_translate)),
    )
    translated = _postprocess_target_language(out, target_lang="English").strip()
    if re.search(r"please provide the text", translated, re.IGNORECASE):
        return comment.strip()
    return translated


def translate_ru_block_to_en(
    settings: Settings,
    *,
    ru_path: str,
    ru_block: str,
) -> str:
    """Translate a RU fragment to EN, preserving fenced code and Liquid blocks."""
    from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
    from ydbdoc_review.translate_postprocess import apply_deterministic_cli_fixes

    out = translate_markdown(
        settings,
        source_lang="Russian",
        target_lang="English",
        source_path=ru_path,
        source_text=ru_block,
    )
    out = restore_markdown_links_from_ru(ru_block, out)
    return apply_deterministic_cli_fixes(out, ru_source=ru_block)


def translate_markdown(
    settings: Settings,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    source_text: str,
    max_output_tokens: int | None = None,
) -> str:
    """
    Translate markdown. Long sources are split into fragments, translated
    separately, then concatenated (see ``YDBDOC_TRANSLATE_CHUNK_CHARS``).

    RU→EN uses block masking so ``` fences and ``{% %}`` blocks stay verbatim.
    """
    instructions = load_translate_instructions(settings)
    target = translate_chunk_target_chars()
    preserve = prose_mask_enabled() and _ru_to_en_preserve_blocks(
        source_lang, target_lang
    )
    model = settings.model_translate
    default_cap = (
        clamp_max_output_tokens(max_output_tokens, model)
        if max_output_tokens is not None
        else _translate_max_output_tokens(model)
    )

    if len(source_text) <= target:
        if preserve:
            return _translate_preserving_blocks(
                settings,
                instructions=instructions,
                source_lang=source_lang,
                target_lang=target_lang,
                source_path=source_path,
                source_text=source_text,
                model=model,
                default_cap=default_cap,
            )
        user_input = _translate_markdown_user_input(
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            source_text=source_text,
        )
        out = _translate_user_payload(
            settings,
            instructions=instructions,
            user_input=user_input,
            reference_for_truncation=source_text,
            model=model,
            max_output_tokens=default_cap,
        )
        return _postprocess_target_language(out, target_lang=target_lang)

    chunks = split_markdown_for_translate(source_text, target_chars=target)
    if len(chunks) == 1:
        if preserve:
            return _translate_preserving_blocks(
                settings,
                instructions=instructions,
                source_lang=source_lang,
                target_lang=target_lang,
                source_path=source_path,
                source_text=chunks[0],
                model=model,
                default_cap=default_cap,
            )
        user_input = _translate_markdown_user_input(
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            source_text=chunks[0],
        )
        out = _translate_user_payload(
            settings,
            instructions=instructions,
            user_input=user_input,
            reference_for_truncation=chunks[0],
            model=model,
            max_output_tokens=default_cap,
        )
        return _postprocess_target_language(out, target_lang=target_lang)

    n = len(chunks)
    parts: list[str] = []
    for i, ch in enumerate(chunks, start=1):
        if preserve:
            parts.append(
                _translate_preserving_blocks(
                    settings,
                    instructions=instructions,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source_path=source_path,
                    source_text=ch,
                    model=model,
                    default_cap=default_cap,
                )
            )
        else:
            parts.append(
                _translate_chunk_with_retry(
                    settings,
                    instructions=instructions,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    source_path=source_path,
                    chunk=ch,
                    chunk_index=i,
                    chunk_count=n,
                    default_cap=default_cap,
                )
            )
    merged = "\n".join(parts)
    merged = _postprocess_target_language(merged, target_lang=target_lang)
    if len(chunks) > 1:
        merged = _maybe_retry_full_document(
            settings,
            instructions=instructions,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            source_text=source_text,
            merged=merged,
            default_cap=default_cap,
        )
    return merged


def _translate_max_output_tokens(model: str) -> int:
    """
    Max tokens for translate model completion.

    Long articles need a large completion budget; the provider still applies its
    own cap and may return an error if the value is unsupported.
    """
    raw = os.environ.get("YDBDOC_TRANSLATE_MAX_OUTPUT_TOKENS", "").strip()
    if raw.isdigit():
        v = int(raw)
        if v == 0:
            requested = _TRANSLATE_OUTPUT_HARD_CEILING
        else:
            requested = max(4096, min(v, _TRANSLATE_OUTPUT_HARD_CEILING))
    else:
        requested = _TRANSLATE_OUTPUT_HARD_CEILING
    return clamp_max_output_tokens(requested, model)


def _max_diff_chars() -> int:
    raw = os.environ.get("YDBDOC_MAX_DIFF_CHARS", "").strip()
    if raw.isdigit():
        return max(4096, int(raw))
    return 120_000


def _markdown_code_fences_balanced(md: str) -> bool:
    """True if ``` fence markers form closed pairs (best-effort truncation check)."""
    lines = md.split("\n")
    open_fence = False
    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            open_fence = not open_fence
    return not open_fence


def _translate_retry_max_tokens(first: int, model: str) -> int:
    return clamp_max_output_tokens(max(first * 2, first + 1), model)


def _full_file_translation_looks_truncated(out: str, source: str) -> bool:
    if len(source) < 4000:
        return False
    if len(out) < int(len(source) * 0.55):
        return True
    return not _markdown_code_fences_balanced(out)


def _diff_en_update_looks_truncated(out: str, en_reference: str) -> bool:
    if len(en_reference) < 8000:
        return False
    if len(out) < int(len(en_reference) * 0.65):
        return True
    return not _markdown_code_fences_balanced(out)


def _cap_block(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[{label} truncated to {limit} chars]\n"


def load_translate_ru_diff_to_en_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "03_translate_ru_diff_to_en.txt")


def load_translate_en_diff_to_ru_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "04_translate_en_diff_to_ru.txt")


def translate_en_update_from_ru_diff(
    settings: Settings,
    *,
    en_reference: str,
    ru_diff: str,
    ru_path: str,
    ru_full: str,
    max_output_tokens: int | None = None,
) -> str:
    """Apply Russian file delta (merge-base..HEAD) onto English with minimal drift."""
    instructions = load_translate_ru_diff_to_en_instructions(settings)
    lim = _max_diff_chars()
    ru_diff_c = _cap_block(ru_diff, lim, "RU_DIFF")
    user_input = (
        f"Russian file path: {ru_path}\n\n"
        f"--- REFERENCE_EN BEGIN ---\n{en_reference}\n--- REFERENCE_EN END ---\n\n"
        f"--- RU_DIFF BEGIN ---\n{ru_diff_c}\n--- RU_DIFF END ---\n\n"
        f"--- RU_FULL BEGIN ---\n{_cap_block(ru_full, lim, 'RU_FULL')}\n--- RU_FULL END ---\n\n"
        "Output only the updated English markdown file."
    )
    model = settings.model_translate
    cap = (
        clamp_max_output_tokens(max_output_tokens, model)
        if max_output_tokens is not None
        else _translate_max_output_tokens(model)
    )
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=cap,
        ).strip()
    )
    if _diff_en_update_looks_truncated(out, en_reference):
        cap2 = _translate_retry_max_tokens(cap, model)
        if cap2 > cap:
            out = _strip_code_fence(
                call_yandex_responses(
                    settings,
                    model,
                    instructions=instructions.strip(),
                    user_input=user_input,
                    max_output_tokens=cap2,
                ).strip()
            )
    if _diff_en_update_looks_truncated(out, en_reference) and _allow_diff_to_full_fallback():
        out = translate_markdown(
            settings,
            source_lang="Russian",
            target_lang="English",
            source_path=ru_path,
            source_text=ru_full,
            max_output_tokens=max_output_tokens,
        )
    return _postprocess_target_language(out, target_lang="English")


def translate_ru_update_from_en_diff(
    settings: Settings,
    *,
    ru_reference: str,
    en_diff: str,
    en_path: str,
    en_full: str,
    max_output_tokens: int | None = None,
) -> str:
    """Apply English file delta (merge-base..HEAD) onto Russian with minimal drift."""
    instructions = load_translate_en_diff_to_ru_instructions(settings)
    lim = _max_diff_chars()
    en_diff_c = _cap_block(en_diff, lim, "EN_DIFF")
    user_input = (
        f"English file path: {en_path}\n\n"
        f"--- REFERENCE_RU BEGIN ---\n{ru_reference}\n--- REFERENCE_RU END ---\n\n"
        f"--- EN_DIFF BEGIN ---\n{en_diff_c}\n--- EN_DIFF END ---\n\n"
        f"--- EN_FULL BEGIN ---\n{en_full}\n--- EN_FULL END ---\n\n"
        "Output only the updated Russian markdown file."
    )
    model = settings.model_translate
    cap = (
        clamp_max_output_tokens(max_output_tokens, model)
        if max_output_tokens is not None
        else _translate_max_output_tokens(model)
    )
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=cap,
        ).strip()
    )
    if _diff_en_update_looks_truncated(out, ru_reference):
        cap2 = _translate_retry_max_tokens(cap, model)
        if cap2 > cap:
            out = _strip_code_fence(
                call_yandex_responses(
                    settings,
                    model,
                    instructions=instructions.strip(),
                    user_input=user_input,
                    max_output_tokens=cap2,
                ).strip()
            )
    if _diff_en_update_looks_truncated(out, ru_reference) and _allow_diff_to_full_fallback():
        out = translate_markdown(
            settings,
            source_lang="English",
            target_lang="Russian",
            source_path=en_path,
            source_text=en_full,
            max_output_tokens=max_output_tokens,
        )
    return out


def load_verify_translation_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "05_verify_translation.txt")


def load_fix_translation_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "06_fix_translation.txt")


def load_confirm_repair_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "07_confirm_repair.txt")


def _translation_self_check_input_cap() -> int:
    raw = os.environ.get("YDBDOC_TRANSLATION_SELF_CHECK_MAX_INPUT_CHARS", "").strip()
    if raw.isdigit():
        return max(4096, int(raw))
    return 55_000


def _translator_confirm_total_input_cap() -> int:
    """Total chars for confirm call (yandexgpt-5.1 input limit ~32k tokens)."""
    raw = os.environ.get("YDBDOC_TRANSLATOR_CONFIRM_MAX_INPUT_CHARS", "").strip()
    if raw.isdigit():
        return max(8000, int(raw))
    return 22_000


def _translation_self_check_max_output_tokens() -> int:
    raw = os.environ.get("YDBDOC_TRANSLATION_SELF_CHECK_MAX_OUTPUT_TOKENS", "").strip()
    if raw.isdigit():
        return max(2048, min(int(raw), 65_536))
    return 16_384


def _cap_verify_body(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = max(cap // 2, cap - 400)
    return (
        text[:head]
        + "\n\n… _[truncated for self-check input size limit]_\n\n"
        + text[-(cap - head) :]
    )


def _qa_extra_blocks(
    *,
    cap: int,
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
) -> str:
    parts: list[str] = []
    if source_pr_number is not None:
        parts.append(f"Исходный PR документации: #{source_pr_number}")
    if ru_pr_diff and ru_pr_diff.strip():
        parts.append(
            "--- RU_PR_DIFF (исходный PR) BEGIN ---\n"
            f"{_cap_verify_body(ru_pr_diff.strip(), min(cap, 24_000))}\n"
            "--- RU_PR_DIFF END ---"
        )
    if en_on_main and en_on_main.strip():
        parts.append(
            "--- EN_ON_MAIN (английский на базовой ветке до перевода) BEGIN ---\n"
            f"{_cap_verify_body(en_on_main.strip(), cap)}\n"
            "--- EN_ON_MAIN END ---"
        )
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n"


def verify_translation_pair(
    settings: Settings,
    *,
    translate_model: str,
    verify_model: str,
    source_lang: str,
    target_lang: str,
    ru_path: str,
    en_path: str,
    source_text: str,
    translated_text: str,
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
) -> str:
    """
    Ask a second model whether the translation matches the source (debug / QA).

    Returns markdown in Russian for posting on the translation PR.
    """
    instructions = load_verify_translation_instructions(settings)
    cap = _translation_self_check_input_cap()
    src_c = _cap_verify_body(source_text.strip(), cap)
    out_c = _cap_verify_body(translated_text.strip(), cap)
    extra = _qa_extra_blocks(
        cap=cap,
        source_pr_number=source_pr_number,
        ru_pr_diff=ru_pr_diff,
        en_on_main=en_on_main,
    )
    user_input = (
        f"Модель перевода: {translate_model}\n"
        f"Модель-проверяющая (вы): {verify_model}\n\n"
        f"Пара файлов: `{ru_path}` (RU) ↔ `{en_path}` (EN)\n"
        f"Исходный язык (SOURCE): {source_lang}\n"
        f"Язык перевода (TRANSLATION): {target_lang}\n\n"
        f"{extra}"
        f"--- SOURCE ({source_lang}) BEGIN ---\n{src_c}\n--- SOURCE END ---\n\n"
        f"--- TRANSLATION ({target_lang}) BEGIN ---\n{out_c}\n--- TRANSLATION END ---\n\n"
        "Следуйте формату ответа из системных инструкций."
    )
    return _strip_code_fence(
        call_yandex_responses(
            settings,
            verify_model,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=_translation_self_check_max_output_tokens(),
        ).strip()
    )


def _repair_max_output_tokens(model: str) -> int:
    """Completion budget for critic full-file repair (may be long)."""
    raw = os.environ.get("YDBDOC_TRANSLATION_REPAIR_MAX_OUTPUT_TOKENS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return clamp_max_output_tokens(int(raw), model)
    return _translate_max_output_tokens(model)


def fix_translation_pair(
    settings: Settings,
    *,
    verify_model: str,
    source_lang: str,
    target_lang: str,
    ru_path: str,
    en_path: str,
    source_text: str,
    translated_text: str,
    review_report: str,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
) -> str:
    """Critic model returns a full corrected translation file."""
    instructions = load_fix_translation_instructions(settings)
    cap = _translation_self_check_input_cap()
    src_c = _cap_verify_body(source_text.strip(), cap)
    tr_c = _cap_verify_body(translated_text.strip(), cap)
    rev_c = _cap_verify_body(review_report.strip(), min(cap, 20_000))
    extra = _qa_extra_blocks(cap=cap, ru_pr_diff=ru_pr_diff, en_on_main=en_on_main)
    user_input = (
        f"Пара файлов: `{ru_path}` (RU) ↔ `{en_path}` (EN)\n"
        f"Исходный язык (SOURCE): {source_lang}\n"
        f"Язык перевода (TRANSLATION): {target_lang}\n\n"
        f"{extra}"
        f"--- SOURCE ({source_lang}) BEGIN ---\n{src_c}\n--- SOURCE END ---\n\n"
        f"--- TRANSLATION ({target_lang}) BEGIN ---\n{tr_c}\n--- TRANSLATION END ---\n\n"
        f"--- REVIEW BEGIN ---\n{rev_c}\n--- REVIEW END ---\n\n"
        "Выведите только исправленный полный markdown перевода."
    )
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            verify_model,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=_repair_max_output_tokens(verify_model),
        ).strip()
    )
    if _full_file_translation_looks_truncated(out, source_text):
        cap2 = clamp_max_output_tokens(
            _repair_max_output_tokens(verify_model) * 2, verify_model
        )
        if cap2 > _repair_max_output_tokens(verify_model):
            out2 = _strip_code_fence(
                call_yandex_responses(
                    settings,
                    verify_model,
                    instructions=instructions.strip(),
                    user_input=user_input,
                    max_output_tokens=cap2,
                ).strip()
            )
            if len(out2) > len(out):
                out = out2
    return out


def confirm_repair_pair(
    settings: Settings,
    *,
    translate_model: str,
    verify_model: str,
    source_lang: str,
    target_lang: str,
    ru_path: str,
    en_path: str,
    source_text: str,
    translation_before: str,
    translation_after: str,
    review_before: str,
    en_on_main: str | None = None,
    ru_pr_diff: str | None = None,
) -> str:
    """Translator model: final check and merge verdict (ПРИНЯТЬ / ОТКЛОНИТЬ)."""
    instructions = load_confirm_repair_instructions(settings)
    total = _translator_confirm_total_input_cap()
    # Share budget: diff + critic review + EN after (primary); smaller RU/before samples.
    per = max(2500, total // 6)
    src_c = _cap_verify_body(source_text.strip(), per)
    before_c = _cap_verify_body(translation_before.strip(), per)
    after_c = _cap_verify_body(translation_after.strip(), per * 2)
    rev_c = _cap_verify_body(review_before.strip(), per * 2)
    extra = _qa_extra_blocks(
        cap=per * 2,
        ru_pr_diff=ru_pr_diff,
        en_on_main=None,
    )
    user_input = (
        f"Модель перевода: {translate_model}\n"
        f"Модель-критик: {verify_model}\n\n"
        f"Пара: `{ru_path}` ↔ `{en_path}`\n"
        f"SOURCE ({source_lang}), целевой язык: {target_lang}\n\n"
        f"{extra}"
        f"--- SOURCE BEGIN ---\n{src_c}\n--- SOURCE END ---\n\n"
        f"--- TRANSLATION_BEFORE BEGIN ---\n{before_c}\n--- TRANSLATION_BEFORE END ---\n\n"
        f"--- TRANSLATION_AFTER BEGIN ---\n{after_c}\n--- TRANSLATION_AFTER END ---\n\n"
        f"--- REVIEW_BEFORE BEGIN ---\n{rev_c}\n--- REVIEW_BEFORE END ---\n\n"
        "Следуйте формату ответа из системных инструкций."
    )
    return _strip_code_fence(
        call_yandex_responses(
            settings,
            translate_model,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=_translation_self_check_max_output_tokens(),
        ).strip()
    )
