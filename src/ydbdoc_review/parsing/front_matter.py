"""YAML front matter parse / serialize for translatable keys."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode

TRANSLATABLE_FRONT_MATTER_KEYS: tuple[str, ...] = ("title", "description")

_KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class FrontMatterValueRecord:
    key: str
    style: str
    value: str
    start_byte: int
    end_byte: int
    block_header_end_byte: int | None
    body_indent: bytes
    newline: bytes
    body_terminated_by_newline: bool = True


def parse_front_matter_with_spans(
    raw: str,
) -> tuple[dict[str, Any], tuple[FrontMatterValueRecord, ...]]:
    data = yaml.safe_load(raw) or {}
    fields = dict(data) if isinstance(data, dict) else {}
    root = yaml.compose(raw)
    if root is None:
        return fields, ()
    if not isinstance(root, MappingNode):
        return {}, ()
    char_bytes = [0]
    for character in raw:
        char_bytes.append(char_bytes[-1] + len(character.encode("utf-8")))
    line_starts = [0]
    for line in raw.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line.encode("utf-8")))
    records: list[FrontMatterValueRecord] = []
    seen_selected: set[str] = set()
    raw_bytes = raw.encode("utf-8")
    value_node_counts: dict[int, int] = {}
    for _key_node, value_node in root.value:
        value_node_counts[id(value_node)] = value_node_counts.get(id(value_node), 0) + 1
    for key_node, value_node in root.value:
        if not isinstance(key_node, ScalarNode):
            continue
        key = key_node.value
        if key not in TRANSLATABLE_FRONT_MATTER_KEYS:
            continue
        if key in seen_selected:
            raise ValueError(f"source_map_invalid_front_matter:{key}")
        seen_selected.add(key)
        value = fields.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if not isinstance(value_node, ScalarNode):
            raise ValueError(f"source_map_invalid_front_matter:{key}")
        if value_node_counts[id(value_node)] != 1 or value_node.start_mark.index < key_node.end_mark.index:
            raise ValueError(f"source_map_invalid_front_matter:{key}")
        style = value_node.style or "plain"
        start_char = value_node.start_mark.index
        end_char = value_node.end_mark.index
        header_end: int | None = None
        body_indent = b""
        newline = b""
        if style in {"'", '"'}:
            start_char += 1
            end_char -= 1
        elif style in {"|", ">"}:
            header_line = value_node.start_mark.line
            if header_line + 1 >= len(line_starts):
                raise ValueError(f"source_map_invalid_front_matter:{key}")
            header_end = line_starts[header_line + 1]
            start_byte_probe = header_end
            end_byte_probe = char_bytes[end_char]
            cursor = start_byte_probe
            indent = bytearray()
            while cursor < end_byte_probe and raw_bytes[cursor] in b" \t\r\n":
                if raw_bytes[cursor] in b" \t":
                    indent.append(raw_bytes[cursor])
                elif indent:
                    break
                cursor += 1
            body_indent = bytes(indent) or b"  "
            body = raw_bytes[start_byte_probe:end_byte_probe]
            newline = b"\r\n" if b"\r\n" in body else b"\n"
            body_terminated_by_newline = body.endswith(b"\n")
            records.append(
                FrontMatterValueRecord(
                    key, style, value, header_end, end_byte_probe,
                    header_end, body_indent, newline,
                    body_terminated_by_newline,
                )
            )
            continue
        records.append(
            FrontMatterValueRecord(
                key, style, value, char_bytes[start_char], char_bytes[end_char],
                header_end, body_indent, newline,
            )
        )
    return fields, tuple(records)


def parse_front_matter(raw: str) -> dict[str, Any]:
    """Parse YAML front matter body (without ``---`` delimiters)."""
    return parse_front_matter_with_spans(raw)[0]


def front_matter_key_order(raw: str) -> list[str]:
    """Preserve key order from the original YAML text."""
    seen: list[str] = []
    for match in _KEY_LINE.finditer(raw):
        key = match.group(1)
        if key not in seen:
            seen.append(key)
    return seen


def dump_front_matter(fields: dict[str, Any], *, key_order: list[str] | None = None) -> str:
    """Serialize front matter fields back to YAML (no delimiters)."""
    order = list(key_order or [])
    for key in fields:
        if key not in order:
            order.append(key)
    ordered: dict[str, Any] = {k: fields[k] for k in order if k in fields}
    body = yaml.dump(
        ordered,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()
    return body + "\n" if body else ""


def apply_front_matter_updates(raw: str, updates: dict[str, str]) -> str:
    """Apply translated ``title`` / ``description``; pass through other keys."""
    if not updates:
        return raw
    source_fields, records = parse_front_matter_with_spans(raw)
    selected = {record.key: record for record in records}
    raw_bytes = raw.encode("utf-8")
    candidate = raw_bytes
    effective_updates: dict[str, str] = {}
    for key, value in sorted(
        ((key, value) for key, value in updates.items() if key in selected),
        key=lambda item: selected[item[0]].start_byte,
        reverse=True,
    ):
        record = selected[key]
        effective_updates[key] = (
            f"{value}\n"
            if record.style in {"|", ">"}
            and record.value.endswith("\n")
            and not value.endswith("\n")
            else value
        )
        candidate = (
            candidate[: record.start_byte]
            + _encode_front_matter_value(record, effective_updates[key])
            + candidate[record.end_byte :]
        )
    candidate_text = candidate.decode("utf-8")
    candidate_fields, candidate_records = parse_front_matter_with_spans(candidate_text)
    expected_fields = dict(source_fields)
    for key, _value in updates.items():
        if key in selected:
            expected_fields[key] = effective_updates[key]
    if candidate_fields != expected_fields:
        key = next((key for key in updates if key in selected), "unknown")
        raise ValueError(f"front_matter_translation_requires_style_change:{key}")
    if tuple((record.key, record.style) for record in candidate_records) != tuple(
        (record.key, record.style) for record in records
    ):
        raise ValueError("source_map_invalid_front_matter:selected_style")
    def canonical(text: bytes, value_records: tuple[FrontMatterValueRecord, ...]) -> bytes:
        output = bytearray()
        cursor = 0
        for record in value_records:
            output.extend(text[cursor : record.start_byte])
            output.extend(b"\x00YDBDOC_TRANSLATABLE:")
            output.extend(f"front_matter:{record.key}".encode("ascii"))
            output.extend(b"\x00")
            cursor = record.end_byte
        output.extend(text[cursor:])
        return bytes(output)
    if canonical(candidate, candidate_records) != canonical(raw_bytes, records):
        raise ValueError("source_map_invalid_front_matter:canonical_digest")
    return candidate_text


def _encode_front_matter_value(record: FrontMatterValueRecord, value: str) -> bytes:
    if record.style == "plain":
        return value.encode("utf-8")
    if record.style == "'":
        return value.replace("'", "''").encode("utf-8")
    if record.style == '"':
        return json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8")
    if record.style == ">" and "\n" in value.rstrip("\n"):
        raise ValueError(f"front_matter_translation_requires_style_change:{record.key}")
    if record.style in {"|", ">"}:
        ends_with_newline = value.endswith("\n") or record.body_terminated_by_newline
        body = value[:-1] if value.endswith("\n") else value
        lines = body.split("\n") if body or value.endswith("\n") else [""]
        if not lines:
            lines = [""]
        output = bytearray()
        for index, line in enumerate(lines):
            output.extend(record.body_indent)
            output.extend(line.encode("utf-8"))
            if index < len(lines) - 1 or ends_with_newline:
                output.extend(record.newline or b"\n")
        return bytes(output)
    raise ValueError(f"front_matter_translation_requires_style_change:{record.key}")


def translatable_front_matter_fields(raw: str) -> dict[str, str]:
    """Return non-empty string values for translatable keys."""
    fields, _records = parse_front_matter_with_spans(raw)
    out: dict[str, str] = {}
    for key in TRANSLATABLE_FRONT_MATTER_KEYS:
        val = fields.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val
    return out
