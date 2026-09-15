"""Language-aware comment spans. Unknown languages stay byte-for-byte intact."""
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


def comment_spans(code: str, info: str) -> list[CommentSpan]:
    language = info.strip().split()[0].lower() if info.strip() else ""
    language = {"yql": "sql", "sh": "bash", "shell": "bash", "py": "python"}.get(language, language)
    if not language:
        return []
    try:
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return []
    spans = []
    # Lexer offsets refer to the original string; strings/docstrings are not comments.
    tokens = list(lexer.get_tokens_unprocessed(code))
    for start, kind, value in tokens:
        if kind not in Comment or kind in Comment.Preproc or kind in Comment.Hashbang:
            continue
        # Some lexers split a multiline comment into several adjacent tokens.
        end = start + len(value)
        if kind in Comment.Multiline and spans and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    bodies = []
    for start, end in spans:
        value = code[start:end]
        for opening, closing in (("/*", "*/"), ("<!--", "-->"), ("(*", "*)")):
            if value.startswith(opening):
                if not value.endswith(closing):
                    return []  # Unterminated comment: do not rewrite this block.
                start += len(opening)
                end -= len(closing)
                break
        else:
            match = re.match(r"(?://|--|#|;|%)+", value)
            if match is None:
                continue
            start += match.end()
        # Keep indentation, line endings and conventional leading '*' outside bodies.
        offset = start
        for line in code[start:end].splitlines(keepends=True):
            match = re.fullmatch(r"([ \t]*(?:\*[ \t]+)?)(.*?)([ \t]*\r?\n|[ \t]*)", line)
            if match and match.group(2):
                a = offset + match.start(2)
                bodies.append(CommentSpan(a, offset + match.end(2)))
            offset += len(line)
    return bodies


def comment_skeleton(code: str, info: str) -> str:
    for span in reversed(comment_spans(code, info)):
        code = code[:span.start] + '<COMMENT>' + code[span.end:]
    return code


def replace_comments(code: str, info: str, replacements: dict[tuple[int, int], str]) -> str:
    original = code
    for span in reversed(comment_spans(code, info)):
        value = replacements.get((span.start, span.end))
        if value is None:
            continue
        if '\n' in value or '\r' in value or any(m in value for m in ('*/', '/*', '-->', '<!--')):
            raise ValueError('comment translation changes comment boundaries')
        code = code[:span.start] + value + code[span.end:]
    if comment_skeleton(original, info) != comment_skeleton(code, info):
        raise ValueError('comment translation changes protected code')
    return code
