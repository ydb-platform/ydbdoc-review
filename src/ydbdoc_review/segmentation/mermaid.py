"""Conservative Mermaid grammar with immutable syntax and offset-based labels.

Unsupported diagrams expose no labels. This is deliberately not a general
Mermaid parser: accepting a label requires recognizing the complete diagram.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class MermaidLabel:
    index: int
    start: int
    end: int
    text: str


_ID = r"[A-Za-z_][A-Za-z_0-9]*"
_ALIAS = re.compile(rf"(?:actor|participant) {_ID}(?: as (?P<label>.+))?")
_MESSAGE = re.compile(rf"{_ID}(?:-->>|->>|-->|->|--x|-x|--\)|-\)){_ID}:[ \t]*(?P<label>.+)")
_NOTE = re.compile(rf"Note (?:over {_ID}(?:,[ \t]*{_ID})?|(?:right|left) of {_ID}):[ \t]*(?P<label>.+)")
_BRANCH = re.compile(r"(?P<keyword>opt|alt|else|loop|par|and|critical|option|break) (?P<label>.+)")
_GRAPH_HEADER = re.compile(r"(?:graph|flowchart) (?:TD|TB|BT|LR|RL)")
_QUOTED = r'''\[(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\]'''
_UNQUOTED = r'''\[[^\[\]"'\r\n()]+\]'''
_GRAPH_LABEL = re.compile(rf"{_QUOTED}|{_UNQUOTED}")
_NODE = rf"{_ID}(?:{_GRAPH_LABEL.pattern})?"
_GRAPH_LINE = re.compile(rf"{_NODE}(?:[ \t]*(?:-->|---|-\.->|==>)[ \t]*{_NODE})*")
_SUBGRAPH = re.compile(rf"subgraph {_ID}(?:{_GRAPH_LABEL.pattern})?")
_INIT = re.compile(r"%%\{init:[ \t]*(?P<config>\{.*\})\}%%")
_COMMENT = re.compile(r"%%[ \t]+(?P<label>.+)")
_TECHNICAL = re.compile(r"\b(?:YDB|life_time)\b")
_ENTITY = re.compile(r"#(?:quot|amp|\d+);")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029`\[\]{}]|%%|~{3,}|--|->|<-|;|<|>")


def _check_label(text: str) -> None:
    # Entity semicolons belong to label text, never statement separators.
    if not text.strip() or _CONTROL.search(_ENTITY.sub("", text)):
        raise ValueError("Unsafe Mermaid label delimiter or control token")


def _parse(content: str) -> tuple[MermaidLabel, ...]:
    labels: list[MermaidLabel] = []
    stack: list[str] = []
    diagram: str | None = None
    offset = 0
    for original_line in content.splitlines(keepends=True):
        line = original_line.rstrip("\r\n")
        stripped = line.strip(" \t")
        start = offset + len(line) - len(line.lstrip(" \t"))
        offset += len(original_line)
        if not stripped:
            continue
        if diagram is None:
            if stripped == "sequenceDiagram":
                diagram = "sequence"
            elif _GRAPH_HEADER.fullmatch(stripped):
                diagram = "graph"
            else:
                raise ValueError("Unsupported Mermaid diagram")
            continue
        if stripped.startswith("%%{"):
            directive = _INIT.fullmatch(stripped)
            if directive is None or not isinstance(json.loads(directive["config"]), dict):
                raise ValueError("Unsupported Mermaid directive")
            # Initialization config is entirely immutable, even string values.
            continue
        if stripped == "end":
            if not stack:
                raise ValueError("Unmatched Mermaid end")
            stack.pop()
            continue
        spans: list[tuple[int, int]] = []
        comment = _COMMENT.fullmatch(stripped)
        if comment:
            spans.append(comment.span("label"))
        elif diagram == "sequence":
            match = _ALIAS.fullmatch(stripped) or _MESSAGE.fullmatch(stripped) or _NOTE.fullmatch(stripped)
            if match is None:
                match = _BRANCH.fullmatch(stripped)
                if match is None:
                    raise ValueError("Unsupported Mermaid sequence line")
                keyword = match["keyword"]
                owner = {"else": "alt", "and": "par", "option": "critical"}.get(keyword)
                if owner:
                    if not stack or stack[-1] != owner:
                        raise ValueError("Misnested Mermaid branch")
                else:
                    stack.append(keyword)
            if match["label"] is not None:
                spans.append(match.span("label"))
        else:
            if _SUBGRAPH.fullmatch(stripped):
                stack.append("subgraph")
            elif not _GRAPH_LINE.fullmatch(stripped):
                raise ValueError("Unsupported Mermaid graph line")
            for match in _GRAPH_LABEL.finditer(stripped):
                delimiter_size = 2 if match.group()[1] in "\"'" else 1
                spans.append((match.start() + delimiter_size, match.end() - delimiter_size))
        for left, right in spans:
            # Trailing spaces are syntax bytes, not translator-owned text.
            right = left + len(stripped[left:right].rstrip(" \t"))
            text = stripped[left:right]
            _check_label(text)
            labels.append(MermaidLabel(len(labels), start + left, start + right, text))
    if diagram is None or stack:
        raise ValueError("Missing header or unclosed Mermaid branch")
    return tuple(labels)


def mermaid_labels(content: str) -> tuple[MermaidLabel, ...]:
    """Return all label spans, in either language, or none for unknown grammar."""
    try:
        return _parse(content)
    except ValueError:
        return ()


def mermaid_skeleton(content: str) -> str:
    """Preserve every syntax byte; raise for unsupported or invalid grammar."""
    for label in reversed(_parse(content)):
        content = content[:label.start] + "\x00LABEL\x00" + content[label.end:]
    return content


def replace_mermaid_labels(content: str, translations: Mapping[int, str]) -> str:
    """Replace immutable original spans after validating all translations."""
    labels = _parse(content)
    if set(translations) - {label.index for label in labels}:
        raise ValueError("Unknown Mermaid label index")
    result = content
    for label in reversed(labels):
        if label.index not in translations:
            continue
        text = translations[label.index]
        _check_label(text)
        if Counter(_TECHNICAL.findall(text)) != Counter(_TECHNICAL.findall(label.text)):
            raise ValueError("Mermaid label lost or changed a technical token")
        # Quotes and a standalone `end` can confuse Mermaid even inside text.
        # Mermaid entities keep these literal and make repeated replacement stable.
        text = text.replace('"', "#quot;").replace("'", "#39;")
        text = re.sub(r"\bend\b", "#101;nd", text)
        result = result[:label.start] + text + result[label.end:]
    if mermaid_skeleton(result) != mermaid_skeleton(content):
        raise ValueError("Mermaid translation changed syntax")
    return result
