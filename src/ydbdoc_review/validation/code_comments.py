"""Language-aware comment spans for fenced code.

The historical :func:`comment_spans` helper intentionally keeps its broad
Pygments behaviour.  Structural assembly uses the explicit approved-language
scanner below, which fails closed for unsupported or malformed syntax.
"""

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Token
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

    tokens = list(lexer.get_tokens_unprocessed(code))
    if not _syntax_is_safe(code, language, tokens):
        return [], False

    raw: list[tuple[int, int, str]] = []
    for start, kind, value in tokens:
        if kind not in Comment or kind in Comment.Preproc or kind in Comment.Hashbang:
            continue
        end = start + len(value)
        raw.append((start, end, value))

    for _start, _end, value in raw:
        if value.startswith("/*") and not value.endswith("*/"):
            return raw, False
    return raw, True


def _yaml_strings_are_balanced(
    tokens: list[tuple[int, Token, str]],
) -> bool:
    """Track YAML quoted scalar delimiters without treating block scalars as strings."""

    quote: str | None = None
    for _start, kind, value in tokens:
        if kind not in Token.Literal.String:
            continue
        index = 0
        while index < len(value):
            char = value[index]
            if quote is None:
                if char in "'\"":
                    quote = char
                index += 1
                continue
            if quote == "'" and value.startswith("''", index):
                index += 2
                continue
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
    return quote is None


def _syntax_is_safe(
    code: str,
    language: str,
    tokens: list[tuple[int, Token, str]],
) -> bool:
    """Reject malformed delimiters while trusting the lexer for token context.

    Pygments is intentionally permissive for recovery. Structural assembly is
    not: a recovered token stream must never make an incomplete source block
    editable. The checks here are deliberately lexical, not language parsing.
    """

    if any(kind in Token.Error for _start, kind, _value in tokens):
        return False

    if language == "python":
        try:
            list(tokenize.generate_tokens(io.StringIO(code).readline))
        except tokenize.TokenError as exc:
            if "string" in str(exc).lower():
                return False

    if language == "yaml" and not _yaml_strings_are_balanced(tokens):
        return False

    if language == "javascript":
        template_open = False
        for _start, kind, value in tokens:
            if kind in Token.Literal.String.Backtick and value == "`":
                template_open = not template_open
        if template_open:
            return False

    if language == "bash":
        for _start, kind, value in tokens:
            if kind in Token.Literal.String.Single:
                quote = "'"
            elif kind in Token.Literal.String.Double:
                quote = '"'
            else:
                continue
            if len(value) < 2 or not value.startswith(quote) or not value.endswith(quote):
                return False

    protected: list[tuple[int, int]] = []
    for start, kind, value in tokens:
        if kind in Comment or kind in Token.Literal.String:
            protected.append((start, start + len(value)))
        if kind in Comment.Multiline and (
            not value.startswith("/*") or not value.endswith("*/")
        ):
            return False

    if language in {"cpp", "java", "javascript"}:
        for marker in ("/*", "*/"):
            index = code.find(marker)
            while index >= 0:
                if not any(start <= index and index + 2 <= end for start, end in protected):
                    return False
                index = code.find(marker, index + 1)
    return True


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
        elif kind in Token.Operator or kind in Token.Punctuation:
            pieces.append(f"<TOKEN:{kind}:{value}>")
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
