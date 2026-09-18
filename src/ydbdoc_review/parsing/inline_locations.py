"""Source provenance for validation-only Markdown inline parsing.

The regular parser intentionally exposes only block line maps.  Editorial
validation needs a narrower contract: every visible inline character must keep
the source span consumed by the configured markdown-it rule that emitted it.
This module installs that opt-in contract without changing the default parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from markdown_it import MarkdownIt
from markdown_it.common.utils import isStrSpace
from markdown_it.parser_block import ParserBlock
from markdown_it.parser_inline import ParserInline
from markdown_it.rules_block.state_block import StateBlock
from markdown_it.rules_core.state_core import StateCore
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token

from ydbdoc_review.parsing.yfm_plugins.image_size import (
    _IMAGE_WITH_SIZE_RE,
)
from ydbdoc_review.parsing.yfm_plugins.image_size import (
    _PLACEHOLDER_PREFIX as _IMAGE_PLACEHOLDER_PREFIX,
)
from ydbdoc_review.parsing.yfm_plugins.image_size import (
    _PLACEHOLDER_SUFFIX as _IMAGE_PLACEHOLDER_SUFFIX,
)

if TYPE_CHECKING:
    from markdown_it.utils import EnvType


_TOKEN_PROJECTION = "__ydbdoc_source_projection__"
_TOKEN_FULL_SPAN = "__ydbdoc_source_span__"


@dataclass(frozen=True)
class SourceSpan:
    """Inclusive/exclusive offsets in the original Markdown source."""

    start: int
    end: int
    leading: int | None = None
    trailing: int | None = None

    @property
    def leading_offset(self) -> int:
        return self.start if self.leading is None else self.leading

    @property
    def trailing_offset(self) -> int:
        return max(self.start, self.end - 1) if self.trailing is None else self.trailing


@dataclass(frozen=True)
class VisibleProjection:
    """Visible text with one parser-owned source span per character."""

    text: str
    spans: tuple[SourceSpan | None, ...]

    def __post_init__(self) -> None:
        if len(self.text) != len(self.spans):
            raise ValueError("visible text and source projection length differ")

    @classmethod
    def empty(cls) -> VisibleProjection:
        return cls("", ())

    def __add__(self, other: VisibleProjection) -> VisibleProjection:
        return VisibleProjection(self.text + other.text, self.spans + other.spans)


class LocatedText(str):
    """A string whose characters retain original source spans through slicing."""

    spans: tuple[SourceSpan | None, ...]

    def __new__(
        cls,
        value: str,
        spans: tuple[SourceSpan | None, ...],
    ) -> LocatedText:
        if len(value) != len(spans):
            raise ValueError("located text and source projection length differ")
        instance = super().__new__(cls, value)
        instance.spans = spans
        return instance

    @classmethod
    def from_source(cls, value: str) -> LocatedText:
        return cls(value, tuple(SourceSpan(index, index + 1) for index in range(len(value))))

    def with_same_length_text(self, value: str) -> LocatedText:
        if len(value) != len(self):
            raise ValueError("source-preserving replacement changed length")
        return LocatedText(value, self.spans)

    def __getitem__(self, key: Any) -> Any:
        value = super().__getitem__(key)
        if isinstance(key, int):
            return value
        return LocatedText(value, self.spans[key])

    def __add__(self, other: str) -> LocatedText:
        if isinstance(other, LocatedText):
            return LocatedText(str(self) + str(other), self.spans + other.spans)
        return LocatedText(str(self) + other, self.spans + (None,) * len(other))

    def __radd__(self, other: str) -> LocatedText:
        if isinstance(other, LocatedText):
            return other + self
        return LocatedText(other + str(self), (None,) * len(other) + self.spans)

    def strip(self, chars: str | None = None) -> LocatedText:
        start = 0
        end = len(self)
        while start < end and _strip_character(self[start], chars):
            start += 1
        while end > start and _strip_character(self[end - 1], chars):
            end -= 1
        return cast(LocatedText, self[start:end])

    def lstrip(self, chars: str | None = None) -> LocatedText:
        start = 0
        while start < len(self) and _strip_character(self[start], chars):
            start += 1
        return cast(LocatedText, self[start:])

    def rstrip(self, chars: str | None = None) -> LocatedText:
        end = len(self)
        while end > 0 and _strip_character(self[end - 1], chars):
            end -= 1
        return cast(LocatedText, self[:end])

    def split(self, sep: str | None = None, maxsplit: Any = -1) -> list[str]:
        if sep is None:
            return _split_whitespace(self, maxsplit)
        if not sep:
            raise ValueError("empty separator")
        pieces: list[str] = []
        start = 0
        splits = 0
        while maxsplit < 0 or splits < maxsplit:
            found = self.find(sep, start)
            if found < 0:
                break
            pieces.append(self[start:found])
            start = found + len(sep)
            splits += 1
        pieces.append(self[start:])
        return pieces


def _strip_character(char: str, chars: str | None) -> bool:
    return char.isspace() if chars is None else char in chars


def _split_whitespace(value: LocatedText, maxsplit: int) -> list[str]:
    pieces: list[str] = []
    index = 0
    splits = 0
    while index < len(value):
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            break
        if maxsplit >= 0 and splits >= maxsplit:
            pieces.append(value[index:].rstrip())
            break
        end = index
        while end < len(value) and not value[end].isspace():
            end += 1
        pieces.append(value[index:end])
        index = end
        splits += 1
    return pieces


def concat_located(parts: list[str]) -> str:
    """Join located slices without discarding their sidecar projection."""
    if not any(isinstance(part, LocatedText) for part in parts):
        return "".join(parts)
    text_parts: list[str] = []
    spans: list[SourceSpan | None] = []
    for part in parts:
        text_parts.append(str(part))
        if isinstance(part, LocatedText):
            spans.extend(part.spans)
        else:
            spans.extend([None] * len(part))
    return LocatedText("".join(text_parts), tuple(spans))


class _LocatedStateBlock(StateBlock):
    def getLines(self, begin: int, end: int, indent: int, keepLastLF: bool) -> str:
        if begin >= end:
            return LocatedText("", ())
        queue: list[str] = []
        for line in range(begin, end):
            line_indent = 0
            line_start = first = self.bMarks[line]
            last = (
                self.eMarks[line] + 1
                if line + 1 < end or keepLastLF
                else self.eMarks[line]
            )
            while first < last and line_indent < indent:
                char = self.src[first]
                if isStrSpace(char):
                    if char == "\t":
                        line_indent += 4 - (line_indent + self.bsCount[line]) % 4
                    else:
                        line_indent += 1
                elif first - line_start < self.tShift[line]:
                    line_indent += 1
                else:
                    break
                first += 1
            if line_indent > indent:
                source = cast(LocatedText, self.src[first - 1 : first])
                padding = LocatedText(
                    " " * (line_indent - indent),
                    source.spans * (line_indent - indent),
                )
                queue.append(padding)
            queue.append(self.src[first:last])
        return concat_located(queue)


class _LocatedParserBlock(ParserBlock):
    def parse(
        self,
        src: str,
        md: MarkdownIt,
        env: EnvType,
        outTokens: list[Token],
    ) -> list[Token] | None:
        if not src:
            return None
        state = _LocatedStateBlock(src, md, env, outTokens)
        self.tokenize(state, state.line, state.lineMax)
        return state.tokens


class _LocatedStateInline(StateInline):
    def __init__(
        self,
        src: str,
        md: MarkdownIt,
        env: EnvType,
        outTokens: list[Token],
    ) -> None:
        self._pending_spans: list[SourceSpan | None] = []
        self._pending = ""
        super().__init__(src, md, env, outTokens)
        self.source_spans = (
            src.spans if isinstance(src, LocatedText) else (None,) * len(src)
        )

    @property
    def pending(self) -> str:
        return self._pending

    @pending.setter
    def pending(self, value: str) -> None:
        old = getattr(self, "_pending", "")
        if old.startswith(value):
            del self._pending_spans[len(value) :]
        self._pending = value

    def pushPending(self) -> Token:
        token = Token("text", "", 0)
        token.content = self.pending
        token.level = self.pendingLevel
        _set_projection(token, VisibleProjection(token.content, tuple(self._pending_spans)))
        self.tokens.append(token)
        self.pending = ""
        self._pending_spans.clear()
        return token


class _LocatedParserInline(ParserInline):
    @classmethod
    def from_parser(cls, parser: ParserInline) -> _LocatedParserInline:
        located = cls()
        located.ruler = parser.ruler
        located.ruler2 = parser.ruler2
        located._extra_terminator_chars = parser._extra_terminator_chars
        located.terminator_re = parser.terminator_re
        return located

    def tokenize(self, state: StateInline) -> None:
        if not isinstance(state, _LocatedStateInline):
            super().tokenize(state)
            return
        rules = self.ruler.getRules("")
        end = state.posMax
        max_nesting = state.md.options["maxNesting"]
        while state.pos < end:
            matched = False
            before = state.pos
            before_token_count = len(state.tokens)
            before_pending_length = len(state.pending)
            if state.level < max_nesting:
                for rule in rules:
                    if rule(state, False):
                        matched = True
                        break
            if matched:
                self._record_rule_result(
                    state,
                    before,
                    state.pos,
                    before_token_count,
                    before_pending_length,
                )
                if state.pos >= end:
                    break
                continue
            state.pending += state.src[state.pos]
            state._pending_spans.append(state.source_spans[state.pos])
            state.pos += 1
        if state.pending:
            state.pushPending()

    def parse(
        self,
        src: str,
        md: MarkdownIt,
        env: EnvType,
        tokens: list[Token],
    ) -> list[Token]:
        state = _LocatedStateInline(src, md, env, tokens)
        self.tokenize(state)
        for rule in self.ruler2.getRules(""):
            rule(state)
        return state.tokens

    def _record_rule_result(
        self,
        state: _LocatedStateInline,
        start: int,
        end: int,
        first_token: int,
        pending_length: int,
    ) -> None:
        pending_added = len(state.pending) - pending_length
        if pending_added > 0:
            state._pending_spans.extend(state.source_spans[end - pending_added : end])

        created = state.tokens[first_token:]
        unprojected = [token for token in created if _projection(token) is None]
        if not unprojected:
            return
        full_span = _cover(state.source_spans[start:end])

        if any(token.type == "image" for token in unprojected):
            for token in unprojected:
                token.meta[_TOKEN_FULL_SPAN] = full_span
            return

        autolink_open = next(
            (
                token
                for token in unprojected
                if token.type == "link_open" and token.markup == "autolink"
            ),
            None,
        )
        if autolink_open is not None:
            inner_spans = tuple(state.source_spans[start + 1 : max(start + 1, end - 1)])
            for token in unprojected:
                token.meta[_TOKEN_FULL_SPAN] = full_span
                if token.type == "text":
                    _set_projection(token, _fit_projection(token.content, inner_spans))
            return

        regular_link_open = next(
            (token for token in unprojected if token.type == "link_open"), None
        )
        if regular_link_open is not None:
            for token in unprojected:
                token.meta[_TOKEN_FULL_SPAN] = full_span
            return

        if len(unprojected) > 1 and all(token.type == "text" for token in unprojected):
            cursor = start
            for token in unprojected:
                length = len(token.content)
                _set_projection(
                    token,
                    VisibleProjection(
                        token.content,
                        tuple(state.source_spans[cursor : cursor + length]),
                    ),
                )
                cursor += length
            return

        for token in unprojected:
            token.meta[_TOKEN_FULL_SPAN] = full_span
            if token.type == "code_inline":
                _set_projection(
                    token,
                    _code_projection(
                        state.src[start:end],
                        tuple(state.source_spans[start:end]),
                        token,
                    ),
                )
            elif token.type in {"softbreak", "hardbreak"}:
                break_span = _first_source_span(state.source_spans[start:end])
                _set_projection(token, VisibleProjection("\n", (break_span,)))
            elif token.type in {"text", "text_special", "html_inline"}:
                _set_projection(
                    token,
                    _fit_projection(
                        token.content,
                        tuple(state.source_spans[start:end]),
                    ),
                )


def _cover(spans: tuple[SourceSpan | None, ...]) -> SourceSpan | None:
    known = [span for span in spans if span is not None]
    if not known:
        return None
    return SourceSpan(
        known[0].start,
        known[-1].end,
        leading=known[0].leading_offset,
        trailing=known[-1].trailing_offset,
    )


def _first_source_span(
    spans: tuple[SourceSpan | None, ...],
) -> SourceSpan | None:
    """Return the first visible source unit consumed by an inline rule."""
    for span in spans:
        if span is not None:
            return span
    return None


def _fit_projection(
    text: str,
    spans: tuple[SourceSpan | None, ...],
) -> VisibleProjection:
    if len(text) == len(spans):
        return VisibleProjection(text, spans)
    covered = _cover(spans)
    return VisibleProjection(text, (covered,) * len(text))


def _code_projection(
    raw: str,
    spans: tuple[SourceSpan | None, ...],
    token: Token,
) -> VisibleProjection:
    marker_length = len(token.markup) or 1
    body = raw[marker_length : len(raw) - marker_length]
    body_spans = list(spans[marker_length : len(spans) - marker_length])
    normalized = body.replace("\n", " ")
    if (
        normalized.startswith(" ")
        and normalized.endswith(" ")
        and normalized.strip()
    ):
        normalized = normalized[1:-1]
        body_spans = body_spans[1:-1]
    if normalized != token.content:
        return _fit_projection(token.content, tuple(body_spans))
    return VisibleProjection(normalized, tuple(body_spans))


def _projection(token: Token) -> VisibleProjection | None:
    value = token.meta.get(_TOKEN_PROJECTION)
    return value if isinstance(value, VisibleProjection) else None


def _set_projection(token: Token, projection: VisibleProjection) -> None:
    token.meta[_TOKEN_PROJECTION] = projection


def _current_text_projection(token: Token) -> VisibleProjection:
    projection = _projection(token)
    if (
        projection is not None
        and projection.text == token.content
        and len(projection.spans) == len(token.content)
    ):
        return projection
    if not token.content:
        return VisibleProjection.empty()
    return VisibleProjection(token.content, (None,) * len(token.content))


def _located_fragments_join(state: StateInline) -> None:
    level = 0
    output: list[Token] = []
    index = 0
    while index < len(state.tokens):
        token = state.tokens[index]
        if token.nesting < 0:
            level -= 1
        token.level = level
        if token.nesting > 0:
            level += 1
        if token.type == "text":
            run = [token]
            index += 1
            while index < len(state.tokens) and state.tokens[index].type == "text":
                run.append(state.tokens[index])
                index += 1
            if len(run) > 1:
                contents = [part.content for part in run]
                projections = [_current_text_projection(part) for part in run]
                token = run[-1]
                token.content = "".join(contents)
                projection = VisibleProjection.empty()
                for part_projection in projections:
                    projection += part_projection
                _set_projection(token, projection)
            else:
                _set_projection(token, _current_text_projection(token))
            token.level = level
            output.append(token)
            continue
        output.append(token)
        index += 1
    state.tokens[:] = output


def _located_text_join(state: StateCore) -> None:
    for inline_token in state.tokens:
        if inline_token.type != "inline":
            continue
        output: list[Token] = []
        for token in inline_token.children or []:
            if token.type == "text_special":
                token.type = "text"
            if token.type == "text" and output and output[-1].type == "text":
                previous = output[-1]
                left = _current_text_projection(previous)
                right = _current_text_projection(token)
                previous.content += token.content
                _set_projection(previous, left + right)
            else:
                output.append(token)
        inline_token.children = output


def _start_source_projection(state: StateCore) -> None:
    state.src = LocatedText.from_source(state.src)


def _located_image_size_preprocess(state: StateCore) -> None:
    src = state.src
    sizes: list[tuple[str, str]] = []
    parts: list[str] = []
    cursor = 0
    for match in _IMAGE_WITH_SIZE_RE.finditer(src):
        parts.append(src[cursor : match.start()])
        index = len(sizes)
        sizes.append((match.group("w"), match.group("h")))
        marker = f"{_IMAGE_PLACEHOLDER_PREFIX}{index}{_IMAGE_PLACEHOLDER_SUFFIX}"
        prefix = src[match.start("prefix") : match.end("prefix")]
        url = src[match.start("url") : match.end("url")]
        rest = src[match.start("rest") : match.end("rest")]
        suffix = src[match.start("suffix") : match.end("suffix")]
        size_span = _cover(
            src.spans[match.start("w") - 2 : match.end("h")]
            if isinstance(src, LocatedText)
            else ()
        )
        marker_text = LocatedText(marker, (size_span,) * len(marker))
        parts.extend([prefix, url, marker_text, rest, suffix])
        cursor = match.end()
    parts.append(src[cursor:])
    state.src = concat_located(parts)
    state.env["__yfm_image_sizes__"] = sizes


_NEWLINE_RE = re.compile(r"\r\n?|\n")


def _located_normalize(state: StateCore) -> None:
    src = state.src
    parts: list[str] = []
    cursor = 0
    for match in _NEWLINE_RE.finditer(src):
        parts.append(src[cursor : match.start()])
        spans = src.spans[match.start() : match.end()] if isinstance(src, LocatedText) else ()
        covered = _cover(spans)
        if covered is not None:
            origin = covered.leading_offset
            covered = SourceSpan(
                covered.start,
                covered.end,
                leading=origin,
                trailing=origin,
            )
        parts.append(LocatedText("\n", (covered,)))
        cursor = match.end()
    parts.append(src[cursor:])
    normalized = concat_located(parts)
    if "\0" in normalized and isinstance(normalized, LocatedText):
        normalized = normalized.with_same_length_text(normalized.replace("\0", "\ufffd"))
    state.src = normalized


def _locate_plain_inline(state: StateCore, token: Token) -> LocatedText | None:
    if token.map is None or not isinstance(state.src, LocatedText):
        return None
    line_starts = [0]
    for match in re.finditer("\n", state.src):
        line_starts.append(match.end())
    start_line, end_line = token.map
    if start_line >= len(line_starts):
        return None
    start = line_starts[start_line]
    end = line_starts[end_line] if end_line < len(line_starts) else len(state.src)
    matches = [match.start() for match in re.finditer(re.escape(token.content), state.src[start:end])]
    if len(matches) != 1:
        return None
    found = start + matches[0]
    return cast(LocatedText, state.src[found : found + len(token.content)])


def _located_inline(state: StateCore) -> None:
    for token in state.tokens:
        if token.type != "inline":
            continue
        source = token.content
        if not isinstance(source, LocatedText):
            source = _locate_plain_inline(state, token) or LocatedText(
                token.content, (None,) * len(token.content)
            )
        token.content = source
        token.children = []
        state.md.inline.parse(source, state.md, state.env, token.children)


def install_inline_location_tracking(md: MarkdownIt) -> None:
    """Install the validation-only source-aware block/inline parse path."""
    md.core.ruler.before(
        "yfm_var_substitute",
        "ydbdoc_source_projection",
        _start_source_projection,
    )
    md.core.ruler.at("yfm_image_size_pre", _located_image_size_preprocess)
    md.core.ruler.at("normalize", _located_normalize)
    md.core.ruler.at("inline", _located_inline)
    md.core.ruler.at("text_join", _located_text_join)
    md.inline.ruler2.at("fragments_join", _located_fragments_join)
    block = _LocatedParserBlock()
    block.ruler = md.block.ruler
    md.block = block
    md.inline = _LocatedParserInline.from_parser(md.inline)


def prose_source_spans(tokens: list[Token]) -> tuple[SourceSpan, ...]:
    """Raw spans of ordinary prose, excluding code, variables and autolinks.

    Use with ``create_parser(source_locations=True)``. Entity/escape tokens are
    deliberately left source-owned; this does not render or normalize Markdown.
    """
    spans: list[SourceSpan] = []
    autolink = False
    for token in tokens:
        if token.type == "link_open":
            autolink = token.markup == "autolink"
        elif token.type == "link_close":
            autolink = False
        elif token.type == "text" and not autolink:
            projection = _projection(token)
            if projection is not None:
                spans.extend(span for span in projection.spans if span is not None)
        if token.children:
            spans.extend(prose_source_spans(token.children))
    return tuple(spans)
