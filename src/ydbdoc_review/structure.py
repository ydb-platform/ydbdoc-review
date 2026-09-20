"""Source-authoritative structural plans for Markdown/YFM translation.

The model is allowed to rewrite prose, list text, table cells, link labels and
image alt text.  The source remains authoritative for every technical part of
the document.  A candidate is accepted only when its block and inline shape
matches the source plan; otherwise the complete affected document is retained
with a diagnostic for the quality loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ydbdoc_review.parsing.markdown_parser import create_parser


@dataclass(frozen=True)
class TextField:
    kind: str
    start: int
    end: int

    @property
    def span(self) -> tuple[int, int]:
        return self.start, self.end


@dataclass(frozen=True)
class DocumentNode:
    kind: str
    start: int
    end: int
    fields: tuple[TextField, ...] = ()
    signature: tuple[str, ...] = ()
    line_start: int = 0
    line_end: int = 0

    @property
    def type(self) -> str:
        return self.kind

    @property
    def editable(self) -> tuple[TextField, ...]:
        return self.fields

    @property
    def mutable(self) -> tuple[TextField, ...]:
        return self.fields


@dataclass(frozen=True)
class DocumentPlan:
    path: str
    source: str
    nodes: tuple[DocumentNode, ...]
    diagnostics: tuple[str, ...] = ()

    @property
    def source_bytes(self) -> bytes:
        return self.source.encode("utf-8")

    @property
    def file_path(self) -> str:
        return self.path


@dataclass(frozen=True)
class AssemblyResult:
    text: str
    diagnostics: tuple[str, ...] = ()
    red: bool = False

    @property
    def candidate(self) -> str:
        return self.text

    @property
    def output(self) -> str:
        return self.text

    @property
    def is_red(self) -> bool:
        return self.red

    @property
    def status(self) -> str:
        return "RED" if self.red else "GREEN"


_LINE_END = re.compile(r"\r\n|\r|\n")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})([^\r\n]*)")
_LIST_MARKER = re.compile(r"^(?P<prefix>[ \t]*)(?P<marker>[-+*]|\d+[.)])(?P<space>[ \t]+)")
_LINK = re.compile(r"(?P<image>!)?\[(?P<label>[^\]\r\n]*)\]\((?:[^()\r\n]|\([^()\r\n]*\))*\)")
_CODE_SPAN = re.compile(r"(?P<ticks>`{1,})(?P<body>[^\r\n]*?)(?P=ticks)")
_TEMPLATE = re.compile(r"\{\{.*?\}\}|\$\{[^}\r\n]*\}", re.S)
_YFM_TAG = re.compile(r"\{%\s*(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?:\s+[^%]*?)?\s*%\}")
_URL = re.compile(r"(?<![\w])(?:https?://|ftp://|mailto:|www\.)[^\s<>\]\}]+", re.I)
_PATH = re.compile(
    r"(?<![\w])(?:\.\.?/|/)[^\s<>\[\]{}(),]+"
    r"|(?<![\w./])(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+(?:#[A-Za-z0-9_.-]+)?"
)
_HTML = re.compile(r"</?[A-Za-z][^>\r\n]*>|<!--.*?-->", re.S)
_ANCHOR = re.compile(r"\{#[^}\r\n]+\}")
_MARKUP = re.compile(
    r"(?P<marker>\*\*|~~|__|(?<!\w)\*|(?<!\w)_)"
    r"(?P<markup_body>[^\r\n]+?)"
    r"(?P=marker)(?!\w)"
)
_UNKNOWN_YFM = re.compile(r"\{%\s*(?P<name>[A-Za-z][A-Za-z0-9_-]*)\b[^%]*%\}")
_NESTED_FENCE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*)$")

_KNOWN_YFM = {
    "else",
    "elsif",
    "endcut",
    "endif",
    "endnote",
    "endtab",
    "endtabs",
    "if",
    "include",
    "note",
    "tab",
    "tabs",
    "term",
    "cut",
}

_ROOT_KINDS = {
    "paragraph_open": "paragraph",
    "heading_open": "heading",
    "fence": "code",
    "code_block": "code",
    "front_matter": "yaml",
    "table_open": "table",
    "bullet_list_open": "list",
    "ordered_list_open": "list",
    "blockquote_open": "blockquote",
    "html_block": "html",
    "hr": "technical",
    "yfm_include": "include",
    "yfm_note_open": "yfm",
    "yfm_tabs_open": "yfm",
    "yfm_if_open": "yfm",
    "yfm_cut_open": "yfm",
    "term_definition_open": "yfm",
}


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    offsets.extend(match.end() for match in _LINE_END.finditer(source))
    if offsets[-1] != len(source):
        offsets.append(len(source))
    return offsets


def _unknown_yfm(source: str) -> str | None:
    for match in _UNKNOWN_YFM.finditer(source):
        if match.group("name").lower() not in _KNOWN_YFM:
            return match.group("name")
    return None


def _append_field(fields: list[TextField], kind: str, start: int, end: int) -> None:
    if end > start:
        fields.append(TextField(kind, start, end))


def _iter_lines(raw: str) -> list[tuple[int, int, str]]:
    """Return ``(start, full_line_end, line_without_newline)`` in source offsets."""
    lines: list[tuple[int, str, str]] = []
    cursor = 0
    for match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", raw):
        if match.start() >= len(raw):
            break
        full = match.group(0)
        lines.append((cursor, cursor + len(full), full.rstrip("\r\n")))
        cursor += len(full)
    return lines


def _scan_inline(
    text: str,
    base: int,
    *,
    technical_code: bool = False,
) -> tuple[list[TextField], tuple[str, ...]]:
    fields: list[TextField] = []
    signature: list[str] = []
    if text.count("[") != text.count("]"):
        signature.append("unbalanced-brackets")
    cursor = 0
    pattern = re.compile(
        "|".join(
            (
                _LINK.pattern,
                _CODE_SPAN.pattern,
                _TEMPLATE.pattern,
                _YFM_TAG.pattern,
                _URL.pattern,
                _PATH.pattern,
                _HTML.pattern,
                _ANCHOR.pattern,
                _MARKUP.pattern,
            )
        ),
        re.S,
    )
    for match in pattern.finditer(text):
        if match.start() < cursor:
            continue
        _append_field(fields, "text", base + cursor, base + match.start())
        if match.group("label") is not None:
            kind = "image_alt" if match.group("image") else "link_label"
            label_start, label_end = match.span("label")
            signature.append("image" if match.group("image") else "link")
            label_fields, label_signature = _scan_inline(
                match.group("label"),
                base + label_start,
                technical_code=True,
            )
            if label_fields:
                fields.extend(label_fields)
            else:
                _append_field(fields, kind, base + label_start, base + label_end)
            signature.extend(f"link-label:{item}" for item in label_signature)
        elif match.group("ticks") is not None:
            marker = match.group("ticks")
            if technical_code:
                signature.append(f"code:{len(marker)}:{match.group('body')}")
            else:
                signature.append(f"code:{len(marker)}")
        elif match.group("name") is not None:
            name = match.group("name").lower()
            if name not in _KNOWN_YFM:
                signature.append("unknown-yfm")
            else:
                signature.append(f"yfm:{name}")
        elif match.group(0).startswith("{{") or match.group(0).startswith("${"):
            signature.append("template")
        elif _URL.fullmatch(match.group(0)):
            signature.append("url")
        elif _PATH.fullmatch(match.group(0)):
            signature.append("path")
        elif match.group(0).startswith("<"):
            signature.append("html")
        elif match.group(0).startswith("{#"):
            signature.append("anchor")
        elif match.group("marker") is not None:
            marker = match.group("marker")
            body_start, _body_end = match.span("markup_body")
            signature.append(f"markup:{marker}")
            inner, inner_signature = _scan_inline(
                match.group("markup_body"),
                base + body_start,
                technical_code=technical_code,
            )
            fields.extend(inner)
            signature.extend(inner_signature)
        else:
            signature.append("markup")
        cursor = match.end()
    _append_field(fields, "text", base + cursor, base + len(text))
    return fields, tuple(signature)


def _scan_table(raw: str, base: int) -> tuple[list[TextField], tuple[str, ...]]:
    fields: list[TextField] = []
    signature: list[str] = ["table"]
    for cursor, _line_end, line_body in _iter_lines(raw):
        if re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", line_body):
            signature.append("separator")
            continue
        pieces = line_body.split("|")
        first = 1 if pieces and pieces[0].strip() == "" else 0
        last = len(pieces) - 1 if pieces and pieces[-1].strip() == "" else len(pieces)
        cells = pieces[first:last]
        signature.append(f"row:{len(cells)}")
        piece_cursor = 0
        for index, piece in enumerate(pieces):
            if index < first or index >= last:
                piece_cursor += len(piece) + 1
                continue
            cell_start = cursor + piece_cursor
            inner, inner_signature = _scan_inline(piece, base + cell_start)
            fields.extend(inner)
            signature.extend(inner_signature)
            piece_cursor += len(piece) + 1
    return fields, tuple(signature)


def _scan_lines(raw: str, base: int, *, list_mode: bool = False) -> tuple[list[TextField], tuple[str, ...]]:
    fields: list[TextField] = []
    signature: list[str] = []
    lines = _iter_lines(raw)
    index = 0
    while index < len(lines):
        cursor, _line_end, body = lines[index]
        prefix_end = 0
        if list_mode:
            marker = _LIST_MARKER.match(body)
            if marker:
                prefix_end = marker.end()
                signature.append("list:ordered" if marker.group("marker")[0].isdigit() else "list:bullet")
        else:
            quote = re.match(r"^[ \t]*> ?", body)
            if quote:
                prefix_end = quote.end()
                signature.append("quote")
        content = body[prefix_end:]

        nested = _NESTED_FENCE.match(content)
        if nested:
            fence = nested.group("fence")
            closing = index + 1
            while closing < len(lines):
                close_body = lines[closing][2]
                if re.fullmatch(rf"[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", close_body):
                    break
                closing += 1
            if closing < len(lines):
                code_end = lines[closing][1]
            else:
                code_end = len(raw)
            code_start = cursor
            code_text = raw[code_start:code_end]
            language = (nested.group("info").strip().split() or [""])[0].lower()
            signature.append(
                f"nested-fence:{fence[0]}:{len(fence)}:{language}:{code_text}"
            )
            index = closing + 1 if closing < len(lines) else len(lines)
            continue

        inner, inner_signature = _scan_inline(content, base + cursor + prefix_end)
        fields.extend(inner)
        signature.extend(inner_signature)
        index += 1
    return fields, tuple(signature)


def _node_for_token(source: str, offsets: list[int], token: object, token_type: str) -> DocumentNode:
    token_map = getattr(token, "map", None)
    assert token_map is not None
    line_start, line_end = token_map
    start, end = offsets[line_start], offsets[line_end]
    raw = source[start:end]
    kind = _ROOT_KINDS[token_type]
    fields: list[TextField] = []
    signature: list[str] = [kind]
    if kind == "heading":
        heading = re.match(r"^[ \t]*#{1,6}[ \t]+", raw)
        setext_lines = _iter_lines(raw)
        setext = (
            len(setext_lines) >= 2
            and bool(re.fullmatch(r"[ \t]*(=+|-+)[ \t]*", setext_lines[1][2]))
        )
        if setext:
            body_start, _body_end, body = setext_lines[0]
            underline = setext_lines[1][2].strip()
            inner, inner_signature = _scan_inline(body, start + body_start)
            fields.extend(inner)
            signature.extend((f"setext:{underline}", *inner_signature))
        else:
            prefix_end = heading.end() if heading else 0
            anchor = re.search(r"\s+\{#[^}\r\n]+\}\s*(?:\r?\n|\r)?$", raw)
            body_end = anchor.start() if anchor else len(raw.rstrip("\r\n"))
            inner, inner_signature = _scan_inline(raw[prefix_end:body_end], start + prefix_end)
            fields.extend(inner)
            heading_match = re.match(r"^[ \t]*(#+)", raw)
            level = len(heading_match.group(1)) if heading_match else 0
            signature.extend((f"level:{level}", *inner_signature))
            if anchor:
                signature.append("anchor")
    elif kind == "table":
        fields, table_signature = _scan_table(raw, start)
        signature.extend(table_signature[1:])
    elif kind == "list":
        fields, list_signature = _scan_lines(raw, start, list_mode=True)
        signature.extend(list_signature)
    elif kind == "blockquote":
        fields, blockquote_signature = _scan_lines(raw, start)
        signature.extend(blockquote_signature)
    elif kind in {"paragraph", "yfm"}:
        fields, inline_signature = _scan_lines(raw, start)
        signature.extend(inline_signature)
    elif kind == "code":
        fence = _FENCE.match(raw)
        if fence:
            language = (fence.group(2).strip().split() or [""])[0].lower()
            signature.append(f"fence:{fence.group(1)[0]}:{len(fence.group(1))}:{language}")
        else:
            signature.append("indented")
    elif kind == "include":
        signature.append("include")
    elif kind == "yaml":
        signature.append("yaml")
    elif kind == "html":
        signature.append("html")
    elif kind == "technical":
        signature.append("technical")
    signature.append(f"lines:{line_end - line_start}")
    return DocumentNode(kind, start, end, tuple(fields), tuple(signature), line_start + 1, line_end)


def _nodes_from_tokens(source: str) -> tuple[tuple[DocumentNode, ...], tuple[str, ...]]:
    unknown = _unknown_yfm(source)
    if unknown is not None:
        return (DocumentNode("unknown", 0, len(source), signature=("unknown",), line_start=1, line_end=source.count("\n") + 1),), (
            f"unknown structure in document: YFM directive {unknown!r} is not supported",
        )
    offsets = _line_offsets(source)
    tokens = create_parser().parse(source)
    nodes: list[DocumentNode] = []
    diagnostics: list[str] = []
    for token in tokens:
        token_type = getattr(token, "type", "")
        if getattr(token, "level", 0) != 0 or token_type not in _ROOT_KINDS:
            continue
        if getattr(token, "map", None) is None:
            diagnostics.append(f"unknown structure in document: root token {token_type!r} has no source range")
            continue
        nodes.append(_node_for_token(source, offsets, token, token_type))
    return tuple(nodes), tuple(diagnostics)


def plan_document(source: str, *, path: str) -> DocumentPlan:
    if path.lower().endswith((".yaml", ".yml")):
        node = DocumentNode(
            "yaml",
            0,
            len(source),
            signature=("yaml", source),
            line_start=1,
            line_end=source.count("\n") + 1,
        )
        return DocumentPlan(path=path, source=source, nodes=(node,))
    try:
        nodes, diagnostics = _nodes_from_tokens(source)
    except Exception as exc:  # parser failures are local, source-preserving diagnostics
        nodes = (DocumentNode("unknown", 0, len(source), signature=("unknown",), line_start=1, line_end=source.count("\n") + 1),)
        diagnostics = (f"unknown structure in {path}: {exc}",)
    return DocumentPlan(path=path, source=source, nodes=nodes, diagnostics=diagnostics)


def _mismatch(plan: DocumentPlan, reason: str) -> AssemblyResult:
    diagnostic = f"structure mismatch in {plan.path}: {reason}"
    return AssemblyResult(plan.source, (*plan.diagnostics, diagnostic), True)


def assemble_document(plan: DocumentPlan, candidate: str) -> AssemblyResult:
    if candidate == plan.source:
        return AssemblyResult(plan.source, plan.diagnostics)
    if plan.diagnostics:
        return _mismatch(plan, "source plan contains an unknown or unreadable node")
    candidate_plan = plan_document(candidate, path=plan.path)
    if candidate_plan.diagnostics:
        return _mismatch(plan, "candidate contains an unknown or unreadable node")
    if len(plan.nodes) != len(candidate_plan.nodes):
        return _mismatch(plan, "candidate block count differs")

    replacements: list[tuple[int, int, str]] = []
    for original, proposed in zip(plan.nodes, candidate_plan.nodes, strict=True):
        if original.kind != proposed.kind:
            return _mismatch(plan, f"candidate block kind differs at line {original.line_start}")
        if original.signature != proposed.signature:
            return _mismatch(plan, f"candidate structure differs at line {original.line_start}")
        if len(original.fields) != len(proposed.fields):
            return _mismatch(plan, f"candidate editable field count differs at line {original.line_start}")
        for source_field, candidate_field in zip(original.fields, proposed.fields, strict=True):
            if source_field.kind != candidate_field.kind:
                return _mismatch(plan, f"candidate field kind differs at line {original.line_start}")
            value = candidate[ candidate_field.start : candidate_field.end ]
            replacements.append((source_field.start, source_field.end, value))

    result = plan.source
    for start, end, value in sorted(replacements, reverse=True):
        result = result[:start] + value + result[end:]
    return AssemblyResult(result, plan.diagnostics, bool(plan.diagnostics))
