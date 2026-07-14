"""Translate Cyrillic in fenced code comments; QA when still present in EN."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.parsing.ast_types import FencedCode
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import DEFAULT_PROMPT_VERSION
from ydbdoc_review.validation.fence_integrity import collect_code_blocks
from ydbdoc_review.validation.finalize_skips import finalize_translate_skip_warning

logger = logging.getLogger(__name__)

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
# Line comments in ydb docs: Go/C++/C#/Java ``//``, Python/shell ``#``, YQL/SQL ``--``.
_COMMENT_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>//|#)(?P<spacing>\s*)(?P<body>.*)$"
)
_SQL_LINE_COMMENT = re.compile(
    r"^(?P<indent>\s*)--(?P<spacing>\s+)(?P<body>.*)$"
)
_SQL_TRAILING_COMMENT = re.compile(r"(?P<prefix>.*?)(?P<marker>\s--\s+)(?P<body>[^\n]*)$")
# Trailing ``//`` on a code line (``panic(err) // comment``), not ``://`` in URLs.
_SLASH_TRAILING_COMMENT = re.compile(r"(?P<prefix>.*?)(?P<marker>\s//\s*)(?P<body>[^\n]*)$")


@dataclass(frozen=True)
class FenceCommentLine:
    block_index: int
    line_index: int
    line: str
    body: str


def _trailing_comment_match(line: str) -> re.Match[str] | None:
    for trail_re in (_SQL_TRAILING_COMMENT, _SLASH_TRAILING_COMMENT):
        m = trail_re.match(line)
        if m is not None:
            return m
    return None


def trailing_comment_code_prefix(line: str) -> str | None:
    """Source code before a trailing ``//`` or ``--`` comment; ``None`` otherwise."""
    m = _trailing_comment_match(line)
    return m.group("prefix") if m is not None else None


def _comment_body_if_cyrillic(line: str) -> str | None:
    for matcher in (_COMMENT_LINE.match, _SQL_LINE_COMMENT.match):
        m = matcher(line)
        if m is None:
            continue
        body = m.group("body")
        if body.strip() and _CYRILLIC.search(body):
            return body
    trail = _trailing_comment_match(line)
    if trail is not None:
        body = trail.group("body")
        if body.strip() and _CYRILLIC.search(body):
            return body
    return None


def _replace_comment_body(line: str, new_body: str, *, old_body: str | None = None) -> str:
    m = _COMMENT_LINE.match(line)
    if m:
        spacing = m.group("spacing") or " "
        return (
            f"{m.group('indent')}{m.group('marker')}{spacing}{new_body.lstrip()}"
        )
    m = _SQL_LINE_COMMENT.match(line)
    if m:
        spacing = m.group("spacing") or " "
        return f"{m.group('indent')}--{spacing}{new_body.lstrip()}"
    trail = _trailing_comment_match(line)
    if trail is not None:
        body = trail.group("body")
        if old_body is not None and body.strip() != old_body.strip():
            return line
        return f"{trail.group('prefix')}{trail.group('marker')}{new_body.lstrip()}"
    return line


def collect_cyrillic_fence_comment_lines(text: str) -> list[FenceCommentLine]:
    """Ordered ``//`` / ``#`` / ``--`` comment lines with Cyrillic inside fenced blocks."""
    blocks = collect_code_blocks(parse_markdown(text))
    found: list[FenceCommentLine] = []
    for block_index, block in enumerate(blocks, start=1):
        for line_index, line in enumerate(block.content.splitlines()):
            body = _comment_body_if_cyrillic(line)
            if body is not None:
                found.append(
                    FenceCommentLine(
                        block_index=block_index,
                        line_index=line_index,
                        line=line,
                        body=body,
                    )
                )
    return found


def translate_cyrillic_fence_comments(
    text: str,
    translate_fn: Callable[[str], str],
) -> str:
    """Replace Cyrillic bodies of ``//`` / ``#`` / ``--`` comment lines inside fences."""
    doc = parse_markdown(text)
    blocks = collect_code_blocks(doc)
    if not blocks:
        return text
    changed = False
    for block in blocks:
        lines = block.content.splitlines()
        block_changed = False
        for line_index, line in enumerate(lines):
            body = _comment_body_if_cyrillic(line)
            if body is None:
                continue
            translated = translate_fn(body.strip()).strip()
            if not translated or translated == body.strip():
                continue
            new_line = _replace_comment_body(line, translated, old_body=body)
            if new_line != line:
                lines[line_index] = new_line
                block_changed = True
        if block_changed:
            block.content = "\n".join(lines)
            changed = True
    return render_markdown(doc) if changed else text


def _iter_fence_comment_lines_in_text(text: str):
    """Yield (block_no, line_no, line) for Cyrillic comment lines in raw markdown."""
    lines = text.splitlines()
    in_fence = False
    fence_char = ""
    block_no = 0
    line_no = 0
    for line in lines:
        m = _FENCE_OPEN.match(line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                block_no += 1
                line_no = 0
            elif marker[0] == fence_char:
                in_fence = False
            continue
        if in_fence:
            line_no += 1
            if _comment_body_if_cyrillic(line) is not None:
                yield block_no, line_no, line


def check_cyrillic_in_en_fence_comments(
    target_text: str,
    *,
    target_lang: str,
) -> list[str]:
    """Warn when EN fenced ``//`` / ``#`` / ``--`` comments still contain Cyrillic."""
    if target_lang.lower() != "en":
        return []
    all_items = list(_iter_fence_comment_lines_in_text(target_text))
    if not all_items:
        return []
    warnings: list[str] = []
    seen: set[str] = set()
    for block_no, line_no, line in all_items[:8]:
        body = _comment_body_if_cyrillic(line) or ""
        snippet = body.strip().replace("\n", " ")[:80]
        if snippet in seen:
            continue
        seen.add(snippet)
        warnings.append(
            "cyrillic_in_fence: "
            f"block {block_no} line {line_no}: «{snippet}»"
        )
    if len(all_items) > 8:
        warnings.append(
            "cyrillic_in_fence: "
            f"… и ещё {len(all_items) - 8} строк с кириллицей в комментариях"
        )
    return warnings


def _parse_comment_translate_response(
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
        raise LLMParseError(f"fence comment translate JSON invalid: {exc}") from exc
    comments = data.get("comments")
    if not isinstance(comments, list):
        raise LLMParseError("fence comment translate: missing comments[]")
    out: dict[str, str] = {}
    for item in comments:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        body = item.get("text")
        if isinstance(cid, str) and isinstance(body, str):
            out[cid] = body.strip()
    missing = expected_ids - set(out)
    if missing:
        raise LLMParseError(
            f"fence comment translate: missing ids {sorted(missing)}"
        )
    return out


def translate_cyrillic_fence_comments_with_client(
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
    """LLM batch translate for Cyrillic ``//`` / ``#`` / ``--`` lines inside fences."""
    items = collect_cyrillic_fence_comment_lines(text)
    if not items:
        return text

    payload = {
        "comments": [
            {
                "id": f"b{item.block_index}-l{item.line_index}",
                "text": item.body.strip(),
            }
            for item in items
        ]
    }
    expected_ids = {entry["id"] for entry in payload["comments"]}
    system = (
        "You translate Russian code comments to English for technical documentation. "
        "Return JSON only: {\"comments\": [{\"id\": \"...\", \"text\": \"...\"}]}. "
        "Keep numbers, punctuation, code identifiers, URLs, and English terms unchanged. "
        "Translate only natural-language words."
    )
    user = (
        f"File: {file_path or '(unknown)'}\n"
        f"Direction: {source_lang} → {target_lang}\n"
        f"Glossary (YAML):\n{glossary.to_prompt_yaml()}\n\n"
        f"Comments JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    model_chain = client.model_chain_for_role("translate")
    last_exc: Exception | None = None
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
            mapping = _parse_comment_translate_response(
                result.content,
                expected_ids=expected_ids,
            )
            break
        except (LLMParseError, Exception) as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "Fence comment translate failed (model=%s): %s",
                model,
                exc,
            )
    else:
        warning = finalize_translate_skip_warning("fence_comment", last_exc or RuntimeError("unknown"))
        logger.warning("Fence comment translate skipped: %s", warning)
        if out_warnings is not None:
            out_warnings.append(warning)
        return text

    def _lookup(body: str, item: FenceCommentLine) -> str:
        key = f"b{item.block_index}-l{item.line_index}"
        return mapping.get(key, body.strip())

    doc = parse_markdown(text)
    blocks = collect_code_blocks(doc)
    changed = False
    for item in items:
        block = blocks[item.block_index - 1]
        lines = block.content.splitlines()
        if item.line_index >= len(lines):
            continue
        translated = _lookup(item.body, item)
        new_line = _replace_comment_body(
            lines[item.line_index], translated, old_body=item.body
        )
        if new_line != lines[item.line_index]:
            lines[item.line_index] = new_line
            block.content = "\n".join(lines)
            changed = True
    return render_markdown(doc) if changed else text


def _text_fence_lang(info: str) -> str:
    parts = (info or "").strip().split()
    return parts[0].lower() if parts else ""


def collect_cyrillic_text_fence_lines(text: str) -> list[FenceCommentLine]:
    """Cyrillic lines inside `` ```text `` fenced diagram blocks."""
    blocks = collect_code_blocks(parse_markdown(text))
    found: list[FenceCommentLine] = []
    for block_index, block in enumerate(blocks, start=1):
        if not isinstance(block, FencedCode):
            continue
        if _text_fence_lang(block.info) != "text":
            continue
        for line_index, line in enumerate(block.content.splitlines()):
            if _CYRILLIC.search(line):
                found.append(
                    FenceCommentLine(
                        block_index=block_index,
                        line_index=line_index,
                        line=line,
                        body=line.strip(),
                    )
                )
    return found


def check_cyrillic_in_en_text_fences(target_text: str, *, target_lang: str) -> list[str]:
    """Residual Cyrillic inside `` ```text `` diagram fences."""
    if target_lang.lower() != "en":
        return []
    items = collect_cyrillic_text_fence_lines(target_text)
    if not items:
        return []
    warnings: list[str] = []
    for item in items[:8]:
        preview = item.body.replace("\n", " ")[:120]
        warnings.append(f"cyrillic_in_text_fence: «{preview}»")
    if len(items) > 8:
        warnings.append(f"… and {len(items) - 8} more cyrillic_in_text_fence lines")
    return warnings


def translate_cyrillic_text_fences_with_client(
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
    """LLM-translate Cyrillic labels inside `` ```text `` diagram fences."""
    del prompt_version
    items = collect_cyrillic_text_fence_lines(text)
    if not items:
        return text

    payload = {
        "lines": [
            {
                "id": f"b{item.block_index}-l{item.line_index}",
                "text": item.body,
            }
            for item in items
        ]
    }
    expected_ids = {entry["id"] for entry in payload["lines"]}
    system = (
        "You translate Russian diagram labels inside ASCII tree diagrams to English. "
        "Return JSON only: {\"lines\": [{\"id\": \"...\", \"text\": \"...\"}]}. "
        "Preserve tree characters (│, ├, └, →), identifiers, and English tokens. "
        "Translate only natural-language words and phrases."
    )
    user = (
        f"File: {file_path or '(unknown)'}\n"
        f"Direction: {source_lang} → {target_lang}\n"
        f"Glossary (YAML):\n{glossary.to_prompt_yaml()}\n\n"
        f"Lines JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    model_chain = client.model_chain_for_role("translate")
    last_exc: Exception | None = None
    mapping: dict[str, str] = {}
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
            data = json.loads(result.content.strip())
            for item in data.get("lines", []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    body = item.get("text")
                    if isinstance(body, str):
                        mapping[item["id"]] = body.strip()
            missing = expected_ids - set(mapping)
            if missing:
                raise LLMParseError(
                    f"text fence translate: missing ids {sorted(missing)}"
                )
            break
        except (LLMParseError, json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "Text fence translate failed (model=%s): %s",
                model,
                exc,
            )
    else:
        warning = finalize_translate_skip_warning("text_fence", last_exc or RuntimeError("unknown"))
        logger.warning("Text fence translate skipped: %s", warning)
        if out_warnings is not None:
            out_warnings.append(warning)
        return text

    doc = parse_markdown(text)
    blocks = collect_code_blocks(doc)
    changed = False
    for item in items:
        block = blocks[item.block_index - 1]
        lines = block.content.splitlines()
        if item.line_index >= len(lines):
            continue
        key = f"b{item.block_index}-l{item.line_index}"
        translated = mapping.get(key, item.body)
        if _CYRILLIC.search(translated):
            continue
        if translated != lines[item.line_index]:
            lines[item.line_index] = translated
            block.content = "\n".join(lines)
            changed = True
    return render_markdown(doc) if changed else text
