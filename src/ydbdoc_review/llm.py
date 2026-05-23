from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from ydbdoc_review.config import Settings, fm_base_url_requires_yandex_folder
from ydbdoc_review.fm_progress import fm_call_span


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


def translation_verify_model_fallbacks() -> tuple[str, ...]:
    """Critic fallbacks. Default: non-Yandex families (Qwen, DeepSeek)."""
    raw = os.environ.get(
        "YDBDOC_MODEL_VERIFY_FALLBACKS",
        "qwen3-235b-a22b/latest,deepseek-v3.2/latest",
    )
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _expand_model_candidates(
    primary: str, fallbacks: tuple[str, ...] | None
) -> list[str]:
    chain: list[str] = []
    for m in (primary, *(fallbacks or ())):
        if m and m not in chain:
            chain.append(m)
    return chain


def _fm_model_not_found(exc: BaseException) -> bool:
    low = str(exc).lower()
    return "failed to get model" in low or "model_call_error" in low


def call_yandex_responses(
    settings: Settings,
    model: str,
    instructions: str,
    user_input: str,
    max_output_tokens: int,
    *,
    model_fallbacks: tuple[str, ...] | None = None,
    operation: str = "fm",
    detail: str = "",
) -> str:
    """One FM request with retry on «model not found» across fallbacks; logs progress."""
    chain = _expand_model_candidates(model, model_fallbacks)
    last_err: RuntimeError | None = None
    for i, cand in enumerate(chain):
        try:
            with fm_call_span(operation=operation, model=cand, detail=detail):
                return _call_yandex_responses_impl(
                    settings,
                    cand,
                    instructions=instructions,
                    user_input=user_input,
                    max_output_tokens=max_output_tokens,
                )
        except RuntimeError as exc:
            last_err = exc
            if _fm_model_not_found(exc) and i + 1 < len(chain):
                continue
            raise
    if last_err is not None:
        raise last_err
    raise RuntimeError("No model candidates for Foundation Models call")


def _call_yandex_responses_impl(
    settings: Settings,
    model: str,
    instructions: str,
    user_input: str,
    max_output_tokens: int,
) -> str:
    max_output_tokens = clamp_max_output_tokens(max_output_tokens, model)
    settings.validate_yandex()
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
                snippet = f"\nresponses.create body (truncated): {dump_fn()[:4000]}"
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


def load_translate_segment_instructions(settings: Settings) -> str:
    p = Path(settings.prompts_dir) / "08_translate_segment.txt"
    return _read_prompt(p)


def load_verify_translation_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "05_verify_translation.txt")


def load_fix_translation_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "06_fix_translation.txt")


def load_revalidate_instructions(settings: Settings) -> str:
    return _read_prompt(Path(settings.prompts_dir) / "07_confirm_repair.txt")


_TRANSLATE_OUTPUT_HARD_CEILING = 1_048_576
_KNOWN_MODEL_COMPLETION_CEILINGS: tuple[tuple[str, int], ...] = (
    ("deepseek", 32_768),
    ("qwen", 32_768),
)


def _model_completion_token_ceiling(model: str) -> int:
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


def _qa_input_cap() -> int:
    raw = os.environ.get("YDBDOC_QA_MAX_INPUT_CHARS", "").strip()
    if raw.isdigit():
        return max(4096, int(raw))
    return 55_000


def _qa_output_tokens() -> int:
    raw = os.environ.get("YDBDOC_QA_MAX_OUTPUT_TOKENS", "").strip()
    if raw.isdigit():
        return max(2048, min(int(raw), 65_536))
    return 16_384


def _cap_body(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = max(cap // 2, cap - 400)
    return (
        text[:head]
        + "\n\n… _[truncated for input size limit]_\n\n"
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
        parts.append(f"Source PR: #{source_pr_number}")
    if ru_pr_diff and ru_pr_diff.strip():
        parts.append(
            "--- RU_PR_DIFF BEGIN ---\n"
            f"{_cap_body(ru_pr_diff.strip(), min(cap, 24_000))}\n"
            "--- RU_PR_DIFF END ---"
        )
    if en_on_main and en_on_main.strip():
        parts.append(
            "--- EN_ON_MAIN BEGIN ---\n"
            f"{_cap_body(en_on_main.strip(), cap)}\n"
            "--- EN_ON_MAIN END ---"
        )
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n"


def verify_translation_pair(
    settings: Settings,
    *,
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
    """Critic compare: returns markdown report with one of three verdicts."""
    instructions = load_verify_translation_instructions(settings)
    cap = _qa_input_cap()
    src_c = _cap_body(source_text.strip(), cap)
    out_c = _cap_body(translated_text.strip(), cap)
    extra = _qa_extra_blocks(
        cap=cap,
        source_pr_number=source_pr_number,
        ru_pr_diff=ru_pr_diff,
        en_on_main=en_on_main,
    )
    user_input = (
        f"Files: `{ru_path}` (SOURCE, {source_lang}) ↔ `{en_path}` (TRANSLATION, {target_lang})\n\n"
        f"{extra}"
        f"--- SOURCE BEGIN ---\n{src_c}\n--- SOURCE END ---\n\n"
        f"--- TRANSLATION BEGIN ---\n{out_c}\n--- TRANSLATION END ---\n"
    )
    raw = call_yandex_responses(
        settings,
        settings.model_translation_verify,
        instructions=instructions.strip(),
        user_input=user_input,
        max_output_tokens=_qa_output_tokens(),
        model_fallbacks=translation_verify_model_fallbacks(),
        operation="compare",
        detail=ru_path,
    )
    return _strip_code_fence(raw.strip())


def fix_translation_pair(
    settings: Settings,
    *,
    source_lang: str,
    target_lang: str,
    ru_path: str,
    en_path: str,
    source_text: str,
    translated_text: str,
    review_report: str,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
) -> dict:
    """Fixer call. Returns parsed ``{"fixes": [...]}`` JSON; empty list on parse failure."""
    instructions = load_fix_translation_instructions(settings)
    cap = _qa_input_cap()
    src_c = _cap_body(source_text.strip(), cap)
    tr_c = _cap_body(translated_text.strip(), cap)
    rev_c = _cap_body(review_report.strip(), min(cap, 20_000))
    extra = _qa_extra_blocks(cap=cap, ru_pr_diff=ru_pr_diff, en_on_main=en_on_main)
    user_input = (
        f"Files: `{ru_path}` (SOURCE) ↔ `{en_path}` (TRANSLATION)\n"
        f"SOURCE language: {source_lang}\n"
        f"TRANSLATION language: {target_lang}\n\n"
        f"{extra}"
        f"--- SOURCE BEGIN ---\n{src_c}\n--- SOURCE END ---\n\n"
        f"--- TRANSLATION BEGIN ---\n{tr_c}\n--- TRANSLATION END ---\n\n"
        f"--- REVIEW BEGIN ---\n{rev_c}\n--- REVIEW END ---\n"
    )
    raw = call_yandex_responses(
        settings,
        settings.model_translation_verify,
        instructions=instructions.strip(),
        user_input=user_input,
        max_output_tokens=_qa_output_tokens(),
        model_fallbacks=translation_verify_model_fallbacks(),
        operation="fix",
        detail=ru_path,
    )
    try:
        data = parse_json_object(raw)
    except (json.JSONDecodeError, ValueError):
        return {"fixes": []}
    fixes = data.get("fixes", [])
    if not isinstance(fixes, list):
        return {"fixes": []}
    cleaned: list[dict] = []
    for item in fixes:
        if not isinstance(item, dict):
            continue
        find = item.get("find")
        repl = item.get("replace")
        if not isinstance(find, str) or not isinstance(repl, str) or not find:
            continue
        cleaned.append(
            {
                "find": find,
                "replace": repl,
                "reason": str(item.get("reason", "")),
            }
        )
    return {"fixes": cleaned}


def revalidate_translation_pair(
    settings: Settings,
    *,
    source_lang: str,
    target_lang: str,
    ru_path: str,
    en_path: str,
    source_text: str,
    translated_text: str,
    review_before: str,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
) -> str:
    """Translator re-validates after fixer. Uses prompt 07; same template as prompt 05."""
    instructions = load_revalidate_instructions(settings)
    cap = _qa_input_cap()
    src_c = _cap_body(source_text.strip(), cap)
    tr_c = _cap_body(translated_text.strip(), cap)
    rev_c = _cap_body(review_before.strip(), min(cap, 20_000))
    extra = _qa_extra_blocks(cap=cap, ru_pr_diff=ru_pr_diff, en_on_main=en_on_main)
    user_input = (
        f"Files: `{ru_path}` (SOURCE, {source_lang}) ↔ `{en_path}` (TRANSLATION, {target_lang})\n\n"
        f"{extra}"
        f"--- SOURCE BEGIN ---\n{src_c}\n--- SOURCE END ---\n\n"
        f"--- TRANSLATION BEGIN ---\n{tr_c}\n--- TRANSLATION END ---\n\n"
        f"--- REVIEW_BEFORE BEGIN ---\n{rev_c}\n--- REVIEW_BEFORE END ---\n"
    )
    raw = call_yandex_responses(
        settings,
        settings.model_translate,
        instructions=instructions.strip(),
        user_input=user_input,
        max_output_tokens=_qa_output_tokens(),
        operation="revalidate",
        detail=ru_path,
    )
    return _strip_code_fence(raw.strip())
