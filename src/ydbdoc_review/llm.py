from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from ydbdoc_review.config import Settings, fm_base_url_requires_yandex_folder
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


def _translate_user_payload(
    settings: Settings,
    *,
    instructions: str,
    user_input: str,
    reference_for_truncation: str,
    max_output_tokens: int | None = None,
) -> str:
    """Single FM call + optional retry when the completion looks truncated."""
    cap = max_output_tokens if max_output_tokens is not None else _translate_max_output_tokens()
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            settings.model_translate,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=cap,
        ).strip()
    )
    if _full_file_translation_looks_truncated(out, reference_for_truncation):
        cap2 = _translate_retry_max_tokens(cap)
        if cap2 > cap:
            out = _strip_code_fence(
                call_yandex_responses(
                    settings,
                    settings.model_translate,
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


def _chunk_translate_max_tokens(chunk_len: int) -> int:
    """Completion budget for one fragment (UTF-8 length → rough token budget)."""
    est = max(12_288, int(chunk_len * 2.3))
    return min(est, _TRANSLATE_OUTPUT_HARD_CEILING)


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
    cap = min(default_cap, _chunk_translate_max_tokens(len(chunk)))
    out = _translate_user_payload(
        settings,
        instructions=instructions,
        user_input=user_input,
        reference_for_truncation=chunk,
        max_output_tokens=cap,
    )
    if should_retry_chunk(chunk, out):
        cap2 = min(_translate_retry_max_tokens(cap), default_cap)
        if cap2 > cap:
            out2 = _translate_user_payload(
                settings,
                instructions=instructions,
                user_input=user_input,
                reference_for_truncation=chunk,
                max_output_tokens=cap2,
            )
            if len(out2) > len(out):
                out = out2
    return out


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
    issues = translation_quality_issues(
        source_text, merged, target_lang=target_lang
    )
    retry_codes = {"too_short", "missing_tabs", "unbalanced_fences", "cyrillic_leak"}
    if not retry_codes.intersection(issues):
        return merged
    user_input = _translate_markdown_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        source_text=source_text,
    )
    cap = default_cap
    retry_out = _translate_user_payload(
        settings,
        instructions=instructions,
        user_input=user_input,
        reference_for_truncation=source_text,
        max_output_tokens=cap,
    )
    retry_out = _postprocess_target_language(retry_out, target_lang=target_lang)
    if len(retry_out) > int(len(merged) * 1.05):
        return retry_out
    if translation_quality_issues(source_text, retry_out, target_lang=target_lang):
        if len(retry_out) >= len(merged):
            return retry_out
    return merged


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
    """
    instructions = load_translate_instructions(settings)
    target = translate_chunk_target_chars()
    default_cap = max_output_tokens if max_output_tokens is not None else _translate_max_output_tokens()

    if len(source_text) <= target:
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
            max_output_tokens=default_cap,
        )
        return _postprocess_target_language(out, target_lang=target_lang)

    chunks = split_markdown_for_translate(source_text, target_chars=target)
    if len(chunks) == 1:
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
            max_output_tokens=default_cap,
        )
        return _postprocess_target_language(out, target_lang=target_lang)

    n = len(chunks)
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


def _translate_max_output_tokens() -> int:
    """
    Max tokens for translate model completion.

    Long articles need a large completion budget; the provider still applies its
    own cap and may return an error if the value is unsupported.
    """
    raw = os.environ.get("YDBDOC_TRANSLATE_MAX_OUTPUT_TOKENS", "").strip()
    if raw.isdigit():
        v = int(raw)
        if v == 0:
            return _TRANSLATE_OUTPUT_HARD_CEILING
        return max(4096, min(v, _TRANSLATE_OUTPUT_HARD_CEILING))
    # Default: ask for the largest value we allow here; gateway may clamp lower.
    return _TRANSLATE_OUTPUT_HARD_CEILING


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


def _translate_retry_max_tokens(first: int) -> int:
    return min(max(first * 2, first + 1), _TRANSLATE_OUTPUT_HARD_CEILING)


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
        f"--- RU_FULL BEGIN ---\n{ru_full}\n--- RU_FULL END ---\n\n"
        "Output only the updated English markdown file."
    )
    cap = max_output_tokens if max_output_tokens is not None else _translate_max_output_tokens()
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            settings.model_translate,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=cap,
        ).strip()
    )
    if _diff_en_update_looks_truncated(out, en_reference):
        cap2 = _translate_retry_max_tokens(cap)
        if cap2 > cap:
            out = _strip_code_fence(
                call_yandex_responses(
                    settings,
                    settings.model_translate,
                    instructions=instructions.strip(),
                    user_input=user_input,
                    max_output_tokens=cap2,
                ).strip()
            )
    if _diff_en_update_looks_truncated(out, en_reference):
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
    cap = max_output_tokens if max_output_tokens is not None else _translate_max_output_tokens()
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            settings.model_translate,
            instructions=instructions.strip(),
            user_input=user_input,
            max_output_tokens=cap,
        ).strip()
    )
    if _diff_en_update_looks_truncated(out, ru_reference):
        cap2 = _translate_retry_max_tokens(cap)
        if cap2 > cap:
            out = _strip_code_fence(
                call_yandex_responses(
                    settings,
                    settings.model_translate,
                    instructions=instructions.strip(),
                    user_input=user_input,
                    max_output_tokens=cap2,
                ).strip()
            )
    if _diff_en_update_looks_truncated(out, ru_reference):
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


def _translation_self_check_input_cap() -> int:
    raw = os.environ.get("YDBDOC_TRANSLATION_SELF_CHECK_MAX_INPUT_CHARS", "").strip()
    if raw.isdigit():
        return max(4096, int(raw))
    return 40_000


def _translation_self_check_max_output_tokens() -> int:
    raw = os.environ.get("YDBDOC_TRANSLATION_SELF_CHECK_MAX_OUTPUT_TOKENS", "").strip()
    if raw.isdigit():
        return max(1024, min(int(raw), 65_536))
    return 8192


def _cap_verify_body(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = max(cap // 2, cap - 400)
    return (
        text[:head]
        + "\n\n… _[truncated for self-check input size limit]_\n\n"
        + text[-(cap - head) :]
    )


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
) -> str:
    """
    Ask a second model whether the translation matches the source (debug / QA).

    Returns markdown in Russian for posting on the source PR.
    """
    instructions = load_verify_translation_instructions(settings)
    cap = _translation_self_check_input_cap()
    src_c = _cap_verify_body(source_text.strip(), cap)
    out_c = _cap_verify_body(translated_text.strip(), cap)
    user_input = (
        f"Модель, которой делали перевод (для справки): {translate_model}\n"
        f"Модель-проверяющая (вы): {verify_model}\n\n"
        f"Пара файлов: `{ru_path}` (RU) ↔ `{en_path}` (EN)\n"
        f"Исходный язык статьи: {source_lang}\n"
        f"Язык перевода (целевой): {target_lang}\n\n"
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
