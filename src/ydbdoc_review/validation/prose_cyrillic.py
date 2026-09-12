"""Translate residual Cyrillic in EN prose and inline backticks (outside fences)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import DEFAULT_PROMPT_VERSION
from ydbdoc_review.validation.finalize_skips import finalize_translate_skip_warning

logger = logging.getLogger(__name__)

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
_BACKTICK_CYR = re.compile(r"`([^`]*[а-яА-ЯёЁ][^`]*)`")
_CYRILLIC_WORD = re.compile(r"[а-яА-ЯёЁ][а-яА-ЯёЁ\-]*")


@dataclass(frozen=True)
class ProseCyrillicSpan:
    span_id: str
    text: str
    context: str


def _inside_backtick(line: str, index: int) -> bool:
    before = line[:index]
    return before.count("`") % 2 == 1


def collect_cyrillic_prose_spans(text: str) -> list[ProseCyrillicSpan]:
    """Ordered Cyrillic snippets in prose (fenced bodies excluded)."""
    found: list[ProseCyrillicSpan] = []
    seen: set[str] = set()
    in_fence = False
    fence_char = ""
    span_no = 0

    for line in text.splitlines():
        m = _FENCE_OPEN.match(line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
            elif marker[0] == fence_char:
                in_fence = False
            continue
        if in_fence or not _CYRILLIC.search(line):
            continue

        for match in _BACKTICK_CYR.finditer(line):
            inner = match.group(1).strip()
            if not inner or inner in seen:
                continue
            seen.add(inner)
            span_no += 1
            found.append(
                ProseCyrillicSpan(
                    span_id=f"p{span_no}",
                    text=inner,
                    context=line.strip()[:240],
                )
            )

        for match in _CYRILLIC_WORD.finditer(line):
            if _inside_backtick(line, match.start()):
                continue
            word = match.group(0)
            if word in seen:
                continue
            seen.add(word)
            span_no += 1
            found.append(
                ProseCyrillicSpan(
                    span_id=f"p{span_no}",
                    text=word,
                    context=line.strip()[:240],
                )
            )

    return found


def translate_cyrillic_prose(
    text: str,
    translate_fn: Callable[[ProseCyrillicSpan], str],
) -> str:
    """Replace Cyrillic prose/backtick snippets using ``translate_fn``."""
    spans = collect_cyrillic_prose_spans(text)
    if not spans:
        return text

    mapping: dict[str, str] = {}
    for span in spans:
        translated = translate_fn(span).strip()
        if not translated or translated == span.text:
            continue
        if _CYRILLIC.search(translated):
            continue
        mapping[span.text] = translated

    return _apply_prose_replacements(text, mapping)


def _apply_prose_replacements(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text

    ordered = sorted(mapping.items(), key=lambda item: -len(item[0]))
    out: list[str] = []
    in_fence = False
    fence_char = ""

    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n\r")
        suffix = line[len(body) :]
        m = _FENCE_OPEN.match(body)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
            elif marker[0] == fence_char:
                in_fence = False
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        new_body = body
        for src, tgt in ordered:
            new_body = new_body.replace(src, tgt)
        out.append(new_body + suffix)

    return "".join(out)


def _parse_prose_translate_response(
    raw: str,
    *,
    expected_ids: set[str],
) -> dict[str, str]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"prose cyrillic translate JSON invalid: {exc}") from exc
    spans = data.get("spans")
    if not isinstance(spans, list):
        raise LLMParseError("prose cyrillic translate: missing spans[]")
    out: dict[str, str] = {}
    for item in spans:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        body = item.get("text")
        if isinstance(sid, str) and isinstance(body, str):
            out[sid] = body.strip()
    missing = expected_ids - set(out)
    if missing:
        raise LLMParseError(
            f"prose cyrillic translate: missing ids {sorted(missing)}"
        )
    return out


def translate_cyrillic_prose_with_client(
    text: str,
    client: YandexLLMClient,
    glossary: Glossary,
    *,
    file_path: str = "",
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    out_warnings: list[str] | None = None,
) -> str:
    """LLM batch translate for residual Cyrillic in EN prose/backticks."""
    del prompt_version  # reserved for future prompt templates
    spans = collect_cyrillic_prose_spans(text)
    if not spans:
        return text

    payload = {
        "spans": [
            {
                "id": span.span_id,
                "text": span.text,
                "context": span.context,
            }
            for span in spans
        ]
    }
    expected_ids = {entry["id"] for entry in payload["spans"]}
    system = (
        "You translate residual Russian words in English YDB documentation. "
        "Return JSON only: {\"spans\": [{\"id\": \"...\", \"text\": \"...\"}]}. "
        "Each output text must be English-only (no Cyrillic). "
        "Keep URLs, placeholders, CLI flags, and existing English terms unchanged. "
        "For inline code in backticks, return only the English term (no backticks)."
    )
    user = (
        f"File: {file_path or '(unknown)'}\n"
        f"Direction: {source_lang} → {target_lang}\n"
        f"Glossary (YAML):\n{glossary.to_prompt_yaml()}\n\n"
        f"Spans JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    model_chain = client.model_chain_for_role("translate")
    last_exc: Exception | None = None
    mapping_by_id: dict[str, str] = {}
    for model in model_chain:
        try:
            result = client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
                role="translate",
            )
            mapping_by_id = _parse_prose_translate_response(
                result.content,
                expected_ids=expected_ids,
            )
            break
        except (LLMParseError, Exception) as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "Prose cyrillic translate failed (model=%s): %s",
                model,
                exc,
            )
    else:
        warning = finalize_translate_skip_warning(
            "prose_cyrillic", last_exc or RuntimeError("unknown")
        )
        logger.warning("Prose cyrillic translate skipped: %s", warning)
        if out_warnings is not None:
            out_warnings.append(warning)
        return text

    mapping: dict[str, str] = {}
    for span in spans:
        translated = mapping_by_id.get(span.span_id, "").strip()
        if not translated or translated == span.text:
            continue
        if _CYRILLIC.search(translated):
            continue
        mapping[span.text] = translated

    return _apply_prose_replacements(text, mapping)
