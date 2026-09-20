"""Language-aware comment spans for fenced code.

The historical :func:`comment_spans` helper intentionally keeps its broad
Pygments behaviour.  Structural assembly uses the explicit approved-language
scanner below, which fails closed for unsupported or malformed syntax.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment
from pygments.util import ClassNotFound


@dataclass(frozen=True)
class CommentSpan:
    start: int
    end: int


@dataclass(frozen=True)
class ApprovedCommentScan:
    """Result of the conservative fenced-code comment scanner."""

    spans: tuple[CommentSpan, ...] = ()
    supported: bool = False
    safe: bool = True


_APPROVED_LANGUAGES = {
    "python": "python",
    "python3": "python",
    "py": "python",
    "bash": "bash",
    "sh": "bash",
    "shell": "bash",
    "yaml": "yaml",
    "yml": "yaml",
    "cc": "cpp",
    "cpp": "cpp",
    "c++": "cpp",
    "java": "java",
    "javascript": "javascript",
    "js": "javascript",
    "jsx": "javascript",
    "node": "javascript",
}


def _language(info: str) -> str:
    return info.strip().split()[0].lower() if info.strip() else ""


def _approved_comment_tokens(
    code: str, language: str
) -> tuple[list[tuple[int, int, str]], bool]:
    """Return comment token spans and whether the token stream is safe."""

    try:
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return [], False

    raw: list[tuple[int, int, str]] = []
    for start, kind, value in lexer.get_tokens_unprocessed(code):
        if kind not in Comment or kind in Comment.Preproc or kind in Comment.Hashbang:
            continue
        end = start + len(value)
        if kind in Comment.Multiline and raw and raw[-1][1] == start:
            previous_start, _previous_end, previous_value = raw[-1]
            raw[-1] = (previous_start, end, previous_value + value)
        else:
            raw.append((start, end, value))

    for _start, _end, value in raw:
        if value.startswith("/*") and not value.endswith("*/"):
            return raw, False
    return raw, True


def scan_approved_comments(code: str, info: str) -> ApprovedCommentScan:
    """Scan comments allowed to become translatable fenced-code children.

    Spans cover comment bodies, excluding delimiters, indentation and
    conventional leading ``*`` characters. Unsupported languages and unsafe
    syntax return no editable spans.
    """

    canonical = _APPROVED_LANGUAGES.get(_language(info))
    if canonical is None:
        return ApprovedCommentScan()
    tokens, safe = _approved_comment_tokens(code, canonical)
    if not safe:
        return ApprovedCommentScan(supported=True, safe=False)

    bodies: list[CommentSpan] = []
    for token_start, token_end, value in tokens:
        start, end = token_start, token_end
        if value.startswith("/*"):
            start += 2
            end -= 2
        else:
            marker = re.match(r"(?://|#)+", value)
            if marker is None:
                continue
            start += marker.end()

        offset = start
        for line in code[start:end].splitlines(keepends=True):
            match = re.fullmatch(
                r"([ \t]*(?:\*[ \t]+)?)(.*?)([ \t]*\r?\n|[ \t]*)", line
            )
            if match and match.group(2):
                bodies.append(
                    CommentSpan(offset + match.start(2), offset + match.end(2))
                )
            offset += len(line)
    return ApprovedCommentScan(tuple(bodies), supported=True)


def approved_comment_spans(code: str, info: str) -> list[CommentSpan]:
    """Return editable spans for the approved structural code languages."""

    result = scan_approved_comments(code, info)
    return list(result.spans) if result.supported and result.safe else []


def translatable_comment_spans(code: str, info: str) -> list[CommentSpan]:
    """Compatibility spelling for the narrow approved-language API."""

    return approved_comment_spans(code, info)


def approved_code_skeleton(code: str, info: str) -> str | None:
    """Return a value-independent syntax skeleton for an approved language.

    Code literals and identifiers are deliberately normalized because they are
    source-owned values, while token kinds, punctuation and line structure
    still detect an unsafe code-shape change.
    """

    scan = scan_approved_comments(code, info)
    canonical = _APPROVED_LANGUAGES.get(_language(info))
    if canonical is None or not scan.safe:
        return None
    try:
        lexer = get_lexer_by_name(canonical, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None
    pieces: list[str] = []
    for _start, kind, value in lexer.get_tokens_unprocessed(code):
        if kind in Comment:
            pieces.append(f"<COMMENT:{kind}>")
        elif kind in Comment.Preproc or kind in Comment.Hashbang:
            pieces.append(f"<TOKEN:{kind}>")
        elif kind in (None,):
            pieces.append(value)
        elif str(kind).startswith("Token.Text"):
            pieces.append(re.sub(r"[^\r\n]", " ", value))
        else:
            pieces.append(f"<TOKEN:{kind}>")
    return "".join(pieces)


def comment_spans(code: str, info: str) -> list[CommentSpan]:
    """Return language-aware comment bodies using the historical broad API."""

    language = _language(info)
    language = {"yql": "sql", "sh": "bash", "shell": "bash", "py": "python"}.get(
        language, language
    )
    if not language:
        return []
    try:
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return []
    spans: list[tuple[int, int]] = []
    tokens = list(lexer.get_tokens_unprocessed(code))
    for start, kind, value in tokens:
        if kind not in Comment or kind in Comment.Preproc or kind in Comment.Hashbang:
            continue
        end = start + len(value)
        if kind in Comment.Multiline and spans and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    bodies: list[CommentSpan] = []
    for start, end in spans:
        value = code[start:end]
        for opening, closing in (("/*", "*/"), ("<!--", "-->"), ("(*", "*)")):
            if value.startswith(opening):
                if not value.endswith(closing):
                    return []
                start += len(opening)
                end -= len(closing)
                break
        else:
            match = re.match(r"(?://|--|#|;|%)+", value)
            if match is None:
                continue
            start += match.end()
        offset = start
        for line in code[start:end].splitlines(keepends=True):
            match = re.fullmatch(
                r"([ \t]*(?:\*[ \t]+)?)(.*?)([ \t]*\r?\n|[ \t]*)", line
            )
            if match and match.group(2):
                a = offset + match.start(2)
                bodies.append(CommentSpan(a, offset + match.end(2)))
            offset += len(line)
    return bodies


def comment_skeleton(code: str, info: str) -> str:
    for span in reversed(comment_spans(code, info)):
        code = code[:span.start] + "<COMMENT>" + code[span.end:]
    return code


def replace_comments(code: str, info: str, replacements: dict[tuple[int, int], str]) -> str:
    original = code
    for span in reversed(comment_spans(code, info)):
        value = replacements.get((span.start, span.end))
        if value is None:
            continue
        if "\n" in value or "\r" in value or any(
            marker in value for marker in ("*/", "/*", "-->", "<!--")
        ):
            raise ValueError("comment translation changes comment boundaries")
        code = code[:span.start] + value + code[span.end:]
    if comment_skeleton(original, info) != comment_skeleton(code, info):
        raise ValueError("comment translation changes protected code")
    return code
