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
from ydbdoc_review.translation.errors import TranslationValidationError
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
# Trailing YAML/shell ``#`` (``disk_scope: <x>  # optional``). Prefer after ``//`` / ``--``.
_HASH_TRAILING_COMMENT = re.compile(r"(?P<prefix>.*?)(?P<marker>\s+#\s*)(?P<body>[^\n]*)$")


@dataclass(frozen=True)
class FenceCommentLine:
    block_index: int
    line_index: int
    line: str
    body: str


@dataclass(frozen=True)
class RawFenceCommentSpan:
    id: str
    block_index: int
    line_index: int
    language: str
    start: int
    end: int
    body: str


_HASH_LANGS = {"python", "py", "bash", "sh", "shell", "yaml", "yml", "toml"}
_SLASH_LANGS = {
    "c", "cc", "cpp", "cxx", "csharp", "cs", "go", "java", "javascript",
    "js", "kotlin", "rust", "swift", "typescript", "ts",
}
_DASH_LANGS = {"sql", "yql", "postgresql", "mysql"}
_HTML_LANGS = {"html", "xml"}


def _line_comment_offset(line: str, marker: str) -> int | None:
    quote: str | None = None
    escaped = False
    index = 0
    while index <= len(line) - len(marker):
        char = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote is not None:
            escaped = True
            index += 1
            continue
        if char in {'"', "'", "`"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            index += 1
            continue
        if quote is None and line.startswith(marker, index):
            if marker in {"#", "--"} and index > 0 and not line[index - 1].isspace():
                index += len(marker)
                continue
            return index
        index += 1
    return None


def _raw_fence_regions(text: str) -> list[tuple[int, str, int, int]]:
    """(block index, info language, content start, content end)."""
    regions: list[tuple[int, str, int, int]] = []
    lines = text.splitlines(keepends=True)
    offset = 0
    opening: tuple[str, int, str, int] | None = None
    block_index = 0
    for raw in lines:
        bare = raw.rstrip("\r\n")
        match = re.match(r"^(\s*)(`{3,}|~{3,})(.*)$", bare)
        if match is not None:
            marker = match.group(2)
            if opening is None:
                block_index += 1
                language = match.group(3).strip().split(maxsplit=1)[0].lower()
                opening = (marker[0], len(marker), language, offset + len(raw))
            elif marker[0] == opening[0] and len(marker) >= opening[1]:
                regions.append((block_index, opening[2], opening[3], offset))
                opening = None
        offset += len(raw)
    return regions


def collect_raw_fence_comment_spans(text: str) -> list[RawFenceCommentSpan]:
    """Language-aware exact comment body spans in raw fenced Markdown."""
    spans: list[RawFenceCommentSpan] = []
    for block_index, language, start, end in _raw_fence_regions(text):
        content = text[start:end]
        supported = language in (_HASH_LANGS | _SLASH_LANGS | _DASH_LANGS | _HTML_LANGS)
        if not supported:
            for line_no, line in enumerate(content.splitlines()):
                if _CYRILLIC.search(line) and re.search(r"(?:#|//|/\*|<!--|--)", line):
                    raise TranslationValidationError(
                        "ambiguous Cyrillic fence comment: "
                        f"block={block_index} language={language or '(none)'} line={line_no + 1}"
                    )
            continue
        line_offsets: list[int] = []
        cursor = 0
        for raw_line in content.splitlines(keepends=True):
            line_offsets.append(cursor)
            cursor += len(raw_line)
        if content and (not line_offsets or cursor < len(content)):
            line_offsets.append(cursor)

        candidates: list[tuple[int, int, int]] = []
        if language in _SLASH_LANGS:
            # C-style block comments, including multiline bodies.
            for match in re.finditer(r"/\*(.*?)\*/", content, re.DOTALL):
                prefix = content[: match.start()]
                line_no = prefix.count("\n")
                line_start = prefix.rfind("\n") + 1
                marker_at = _line_comment_offset(
                    content[line_start : match.start() + 2], "/*"
                )
                if marker_at != match.start() - line_start:
                    continue
                candidates.append((match.start(1), match.end(1), line_no))
        if language in _HTML_LANGS:
            for match in re.finditer(r"<!--(.*?)-->", content, re.DOTALL):
                prefix = content[: match.start()]
                line_start = prefix.rfind("\n") + 1
                marker_at = _line_comment_offset(
                    content[line_start : match.start() + 4], "<!--"
                )
                if marker_at != match.start() - line_start:
                    continue
                candidates.append(
                    (
                        match.start(1),
                        match.end(1),
                        content[: match.start()].count("\n"),
                    )
                )

        marker = (
            "#" if language in _HASH_LANGS else
            "//" if language in _SLASH_LANGS else
            "--" if language in _DASH_LANGS else None
        )
        if marker is not None:
            relative = 0
            heredoc_end: str | None = None
            for line_no, raw_line in enumerate(content.splitlines(keepends=True)):
                line = raw_line.rstrip("\r\n")
                if heredoc_end is not None:
                    if line.strip() == heredoc_end:
                        heredoc_end = None
                    relative += len(raw_line)
                    continue
                if language in {"bash", "sh", "shell"}:
                    heredoc = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)", line)
                    if heredoc is not None:
                        heredoc_end = heredoc.group(1)
                marker_at = _line_comment_offset(line, marker)
                if marker_at is not None:
                    body_start = marker_at + len(marker)
                    while body_start < len(line) and line[body_start] in " \t":
                        body_start += 1
                    candidates.append((relative + body_start, relative + len(line), line_no))
                relative += len(raw_line)
        candidates.sort()
        last_end = -1
        for span_start, span_end, line_no in candidates:
            if span_start < last_end:
                continue
            body = content[span_start:span_end]
            last_end = span_end
            if not _CYRILLIC.search(body):
                continue
            spans.append(
                RawFenceCommentSpan(
                    id=f"b{block_index}-l{line_no}",
                    block_index=block_index,
                    line_index=line_no,
                    language=language,
                    start=start + span_start,
                    end=start + span_end,
                    body=body,
                )
            )
    return spans


def _masked_comment_skeleton(text: str, spans: list[RawFenceCommentSpan]) -> str:
    out: list[str] = []
    cursor = 0
    for span in spans:
        out.extend((text[cursor:span.start], f"<COMMENT:{span.id}>"))
        cursor = span.end
    out.append(text[cursor:])
    return "".join(out)


def _trailing_comment_match(line: str) -> re.Match[str] | None:
    for trail_re in (
        _SQL_TRAILING_COMMENT,
        _SLASH_TRAILING_COMMENT,
        _HASH_TRAILING_COMMENT,
    ):
        m = trail_re.match(line)
        if m is not None and _outside_quoted_literal(line, m.start("marker")):
            return m
    return None


def _outside_quoted_literal(line: str, marker_start: int) -> bool:
    """Conservatively reject comment-looking markers inside string literals."""
    quote: str | None = None
    escaped = False
    for char in line[:marker_start]:
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote is not None:
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
    return quote is None


def trailing_comment_code_prefix(line: str) -> str | None:
    """Source code before a trailing ``//`` / ``--`` / ``#`` comment; ``None`` otherwise."""
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


def _strip_json_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _parse_batch_translate_response(
    raw: str,
    *,
    array_key: str,
    expected_ids: set[str],
    error_label: str,
) -> dict[str, str]:
    text = _strip_json_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"{error_label} JSON invalid: {exc}") from exc
    entries = data.get(array_key)
    if not isinstance(entries, list):
        raise LLMParseError(f"{error_label}: missing {array_key}[]")
    out: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        entry_id = item.get("id")
        body = item.get("text")
        if isinstance(entry_id, str) and isinstance(body, str):
            out[entry_id] = body.strip()
    missing = expected_ids - set(out)
    extra = set(out) - expected_ids
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing ids {sorted(missing)}")
        if extra:
            details.append(f"extra ids {sorted(extra)}")
        raise LLMParseError(f"{error_label}: {'; '.join(details)}")
    return out


def _parse_comment_translate_response(
    raw: str,
    *,
    expected_ids: set[str],
) -> dict[str, str]:
    mapping = _parse_batch_translate_response(
        raw,
        array_key="comments",
        expected_ids=expected_ids,
        error_label="fence comment translate",
    )
    invalid = sorted(
        entry_id for entry_id, body in mapping.items() if _CYRILLIC.search(body)
    )
    if invalid:
        raise TranslationValidationError(
            "fence comment translate: Cyrillic remains in EN comments "
            f"{invalid}"
        )
    return mapping


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
    items = collect_raw_fence_comment_spans(text)
    if not items:
        return text

    payload = {
        "comments": [
            {
                "id": item.id,
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
    last_validation_exc: LLMParseError | TranslationValidationError | None = None
    mapping: dict[str, str] | None = None
    for model in model_chain:
        for attempt in range(1, 4):
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
            except (LLMParseError, TranslationValidationError) as exc:
                last_exc = last_validation_exc = exc
                logger.warning(
                    "Fence comment translate validation failed "
                    "(model=%s, attempt=%s/3): %s",
                    model,
                    attempt,
                    exc,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Fence comment translate failed (model=%s): %s",
                    model,
                    exc,
                )
                break
        else:
            continue
        if mapping is not None:
            break
    else:
        if last_validation_exc is not None:
            if isinstance(last_validation_exc, TranslationValidationError):
                raise last_validation_exc
            raise TranslationValidationError(
                str(last_validation_exc)
            ) from last_validation_exc
        warning = finalize_translate_skip_warning(
            "fence_comment", last_exc or RuntimeError("unknown")
        )
        logger.warning("Fence comment translate skipped: %s", warning)
        if out_warnings is not None:
            out_warnings.append(warning)
        return text

    source_skeleton = _masked_comment_skeleton(text, items)
    output = text
    for item in reversed(items):
        translated = mapping[item.id].strip()
        output = output[: item.start] + translated + output[item.end :]
    output_spans = collect_raw_fence_comment_spans(output)
    # Recreate spans for every recognized comment, including those now free of
    # Cyrillic, so the masked byte skeleton can be compared deterministically.
    # Positions shift after translation. Locate each replacement in order
    # between unchanged surrounding bytes rather than trusting model lengths.
    relocated: list[RawFenceCommentSpan] = []
    delta = 0
    for item in items:
        translated = mapping[item.id].strip()
        relocated.append(
            RawFenceCommentSpan(
                id=item.id,
                block_index=item.block_index,
                line_index=item.line_index,
                language=item.language,
                start=item.start + delta,
                end=item.start + delta + len(translated),
                body=translated,
            )
        )
        delta += len(translated) - (item.end - item.start)
    if _masked_comment_skeleton(output, relocated) != source_skeleton:
        raise TranslationValidationError(
            "fence comment translate: non-comment byte skeleton changed"
        )
    remaining = output_spans
    if remaining:
        raise TranslationValidationError(
            "fence comment translate: Cyrillic remains after insertion"
        )
    return output


def _text_fence_lang(info: str) -> str:
    parts = (info or "").strip().split()
    return parts[0].lower() if parts else ""


def _diagram_fence_lang(info: str) -> str | None:
    """Fence languages whose inline labels are translated (``text``, ``mermaid``)."""
    lang = _text_fence_lang(info)
    if lang in {"text", "mermaid"}:
        return lang
    return None


def _preserve_leading_indent(original_line: str, new_content: str) -> str:
    m = re.match(r"^(\s*)", original_line)
    prefix = m.group(1) if m else ""
    return prefix + new_content.lstrip()


def collect_cyrillic_text_fence_lines(text: str) -> list[FenceCommentLine]:
    """Cyrillic lines inside `` ```text `` / `` ```mermaid `` diagram blocks."""
    blocks = collect_code_blocks(parse_markdown(text))
    found: list[FenceCommentLine] = []
    for block_index, block in enumerate(blocks, start=1):
        if not isinstance(block, FencedCode):
            continue
        if _diagram_fence_lang(block.info) is None:
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


def translate_cyrillic_text_fences(
    text: str,
    translate_fn: Callable[[str], str],
) -> str:
    """Replace Cyrillic lines inside `` ```text `` diagram fences."""
    items = collect_cyrillic_text_fence_lines(text)
    if not items:
        return text

    doc = parse_markdown(text)
    blocks = collect_code_blocks(doc)
    changed = False
    for item in items:
        block = blocks[item.block_index - 1]
        lines = block.content.splitlines()
        if item.line_index >= len(lines):
            continue
        translated = translate_fn(item.body.strip()).strip()
        if not translated or _CYRILLIC.search(translated):
            continue
        new_line = _preserve_leading_indent(lines[item.line_index], translated)
        if new_line != lines[item.line_index]:
            lines[item.line_index] = new_line
            block.content = "\n".join(lines)
            changed = True
    return render_markdown(doc) if changed else text


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
            mapping = _parse_batch_translate_response(
                result.content,
                array_key="lines",
                expected_ids=expected_ids,
                error_label="text fence translate",
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

    def _lookup(body: str) -> str:
        for item in items:
            if item.body.strip() == body.strip():
                key = f"b{item.block_index}-l{item.line_index}"
                return mapping.get(key, body.strip())
        return body.strip()

    return translate_cyrillic_text_fences(text, _lookup)
