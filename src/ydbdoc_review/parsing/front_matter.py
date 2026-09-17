"""YAML front matter parse / serialize for translatable keys.

``title`` / ``description`` are translated via surgical value replacement that
preserves keys, comments, delimiters, quote style, block headers/chomping and
every unselected field byte-for-byte (REQUIREMENTS_RU.md §5 / §9).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode

TRANSLATABLE_FRONT_MATTER_KEYS: tuple[str, ...] = ("title", "description")

_KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:", re.MULTILINE)
_BLOCK_STYLES = frozenset({"|", ">"})


@dataclass(frozen=True, slots=True)
class FrontMatterValueRecord:
    """Parser-owned span for one top-level YAML scalar value."""

    key: str
    style: str  # "", "'", '"', "|", ">"
    value: str
    start: int  # inclusive, character index into raw
    end: int  # exclusive
    block_header_end: int | None
    body_indent: str
    newline: str


class FrontMatterError(ValueError):
    """Raised when front matter cannot be updated while preserving style."""


def parse_front_matter_with_spans(
    raw: str,
) -> tuple[dict[str, Any], tuple[FrontMatterValueRecord, ...]]:
    """Parse FM body and return semantic mapping + owned scalar records."""
    if not raw or not raw.strip():
        return {}, ()

    try:
        root = yaml.compose(raw)
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"source_map_invalid_front_matter:{exc}") from exc

    if root is None:
        return {}, ()
    if not isinstance(root, MappingNode):
        return {}, ()

    fields: dict[str, Any] = {}
    records: list[FrontMatterValueRecord] = []
    selected_seen: set[str] = set()
    selected_dupes: set[str] = set()
    line_starts = _line_start_indexes(raw)

    for key_node, value_node in root.value:
        if not isinstance(key_node, ScalarNode):
            continue
        key = key_node.value
        if not isinstance(key, str):
            continue

        if key in TRANSLATABLE_FRONT_MATTER_KEYS:
            if key in selected_seen:
                selected_dupes.add(key)
            selected_seen.add(key)

        if isinstance(value_node, ScalarNode):
            semantic = _scalar_semantic(value_node)
            fields[key] = semantic

            if (
                key in TRANSLATABLE_FRONT_MATTER_KEYS
                and isinstance(semantic, str)
                and semantic.strip()
                and _value_owned_by_key(key_node, value_node)
                and not raw[value_node.start_mark.index:value_node.end_mark.index].startswith(("&", "!"))
            ):
                records.append(
                    _record_from_scalar(raw, key, value_node, line_starts)
                )
        elif isinstance(value_node, MappingNode):
            fields[key] = _mapping_to_plain(value_node)
        else:
            # Sequence / unknown — keep opaque via safe round-trip of subtree.
            fields[key] = yaml.safe_load(
                raw[value_node.start_mark.index : value_node.end_mark.index]
            )

    # Drop span records for duplicated selected keys.
    if selected_dupes:
        records = [r for r in records if r.key not in selected_dupes]

    return fields, tuple(records)


def parse_front_matter(raw: str) -> dict[str, Any]:
    """Parse YAML front matter body (without ``---`` delimiters)."""
    fields, _ = parse_front_matter_with_spans(raw)
    return fields


def front_matter_key_order(raw: str) -> list[str]:
    """Preserve key order from the original YAML text (legacy dump helper)."""
    seen: list[str] = []
    for match in _KEY_LINE.finditer(raw):
        key = match.group(1)
        if key not in seen:
            seen.append(key)
    return seen


def dump_front_matter(fields: dict[str, Any], *, key_order: list[str] | None = None) -> str:
    """Serialize front matter fields back to YAML (no delimiters).

    Standalone helper for tests/tools. One-pass reinsertion must use
    :func:`apply_front_matter_updates` instead.
    """
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
    """Apply translated ``title`` / ``description`` surgically; keep the rest."""
    if not updates:
        return raw

    allowed = {
        key: value
        for key, value in updates.items()
        if key in TRANSLATABLE_FRONT_MATTER_KEYS and isinstance(value, str)
    }
    if not allowed:
        return raw

    source_fields, source_records = parse_front_matter_with_spans(raw)
    by_key = {record.key: record for record in source_records}
    missing = [key for key in allowed if key not in by_key]
    if missing:
        raise FrontMatterError(
            f"front_matter_translation_requires_style_change:{missing[0]}"
        )

    # Descending offsets so earlier indexes stay valid.
    ordered = sorted(
        ((by_key[key], value) for key, value in allowed.items()),
        key=lambda item: item[0].start,
        reverse=True,
    )

    result = raw
    for record, value in ordered:
        encoded = _encode_front_matter_value(record, value)
        start, end = _replaceable_span(record)
        result = result[:start] + encoded + result[end:]

    new_fields, new_records = parse_front_matter_with_spans(result)
    # A semantic dict comparison alone loses duplicate keys and new fields.
    # Compare the exact complement of updated scalar interiors as well.
    def protected_bytes(body: str, records: tuple[FrontMatterValueRecord, ...]) -> list[str]:
        pieces = []
        cursor = 0
        for record in sorted(records, key=lambda r: r.start):
            if record.key not in allowed:
                continue
            start, end = _replaceable_span(record)
            pieces.append(body[cursor:start])
            cursor = end
        pieces.append(body[cursor:])
        return pieces

    if protected_bytes(raw, source_records) != protected_bytes(result, new_records):
        raise FrontMatterError("front_matter_protected_bytes_changed")
    _assert_update_integrity(
        source_fields=source_fields,
        source_records=source_records,
        new_fields=new_fields,
        new_records=new_records,
        updates=allowed,
    )
    return result


def translatable_front_matter_fields(raw: str) -> dict[str, str]:
    """Return non-empty string values for surgically updatable selected keys."""
    _, records = parse_front_matter_with_spans(raw)
    return {record.key: record.value for record in records}


def _scalar_semantic(node: ScalarNode) -> Any:
    tag = node.tag or ""
    if tag.endswith(":null") or node.value is None:
        return None
    if tag.endswith(":bool"):
        return node.value in {"true", "True", "TRUE", "yes", "Yes", "YES"}
    if tag.endswith(":int"):
        try:
            return int(node.value)
        except ValueError:
            return node.value
    if tag.endswith(":float"):
        try:
            return float(node.value)
        except ValueError:
            return node.value
    return node.value


def _mapping_to_plain(node: MappingNode) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, ScalarNode):
            continue
        key = key_node.value
        if isinstance(value_node, ScalarNode):
            out[key] = _scalar_semantic(value_node)
        elif isinstance(value_node, MappingNode):
            out[key] = _mapping_to_plain(value_node)
    return out


def _value_owned_by_key(key_node: ScalarNode, value_node: ScalarNode) -> bool:
    """Reject alias-backed values whose marks belong to another key's anchor."""
    return value_node.start_mark.index >= key_node.end_mark.index


def _line_start_indexes(raw: str) -> list[int]:
    starts = [0]
    for idx, ch in enumerate(raw):
        if ch == "\n":
            starts.append(idx + 1)
    return starts


def _record_from_scalar(
    raw: str,
    key: str,
    node: ScalarNode,
    line_starts: list[int],
) -> FrontMatterValueRecord:
    style = node.style or ""
    start = node.start_mark.index
    end = node.end_mark.index
    block_header_end: int | None = None
    body_indent = ""
    newline = ""

    if style in _BLOCK_STYLES:
        header_line = node.start_mark.line  # 0-based
        if header_line + 1 < len(line_starts):
            block_header_end = line_starts[header_line + 1]
        else:
            block_header_end = end
        body = raw[block_header_end:end]
        newline = "\r\n" if "\r\n" in body[: body.find("\n") + 2] else "\n"
        body_indent = _detect_body_indent(body, key_column=node.start_mark.column)

    return FrontMatterValueRecord(
        key=key,
        style=style,
        value=node.value if isinstance(node.value, str) else str(node.value),
        start=start,
        end=end,
        block_header_end=block_header_end,
        body_indent=body_indent,
        newline=newline,
    )


def _detect_body_indent(body: str, *, key_column: int) -> str:
    for line in body.splitlines():
        if not line.strip():
            continue
        # Leading whitespace of the first non-empty body line.
        i = 0
        while i < len(line) and line[i] in " \t":
            i += 1
        return line[:i]
    return (" " * key_column) + "  "


def _replaceable_span(record: FrontMatterValueRecord) -> tuple[int, int]:
    if record.style in {"'", '"'}:
        return record.start + 1, record.end - 1
    if record.style in _BLOCK_STYLES:
        assert record.block_header_end is not None
        return record.block_header_end, record.end
    return record.start, record.end


def _encode_front_matter_value(record: FrontMatterValueRecord, value: str) -> str:
    style = record.style
    if style == "":
        return value
    if style == "'":
        return value.replace("'", "''")
    if style == '"':
        return json.dumps(value, ensure_ascii=False)[1:-1]
    if style == "|":
        return _encode_block_body(record, value, allow_newlines=True)
    if style == ">":
        if "\n" in value.rstrip("\n") and value.count("\n") > (
            1 if value.endswith("\n") else 0
        ):
            # Interior newlines cannot be represented in a single folded line.
            if any(part for part in value.splitlines()[1:]):
                raise FrontMatterError(
                    f"front_matter_translation_requires_style_change:{record.key}"
                )
        return _encode_block_body(record, value, allow_newlines=False)
    raise FrontMatterError(
        f"front_matter_translation_requires_style_change:{record.key}"
    )


def _encode_block_body(
    record: FrontMatterValueRecord,
    value: str,
    *,
    allow_newlines: bool,
) -> str:
    indent = record.body_indent
    newline = record.newline or "\n"
    lines = value.splitlines()
    if not allow_newlines and len(lines) > 1:
        raise FrontMatterError(
            f"front_matter_translation_requires_style_change:{record.key}"
        )
    if not lines:
        return indent + newline if value.endswith("\n") else ""
    # Each content line is indented; YAML block bodies always end the last
    # physical line with a newline. Header chomping decides the semantic
    # trailing newlines on reparse.
    return "".join(f"{indent}{line}{newline}" for line in lines)


def _assert_update_integrity(
    *,
    source_fields: dict[str, Any],
    source_records: tuple[FrontMatterValueRecord, ...],
    new_fields: dict[str, Any],
    new_records: tuple[FrontMatterValueRecord, ...],
    updates: dict[str, str],
) -> None:
    new_by_key = {record.key: record for record in new_records}
    source_by_key = {record.key: record for record in source_records}

    for key, expected in updates.items():
        if new_fields.get(key) != expected:
            raise FrontMatterError(
                f"front_matter_translation_requires_style_change:{key}"
            )
        if key not in new_by_key or key not in source_by_key:
            raise FrontMatterError(
                f"front_matter_translation_requires_style_change:{key}"
            )
        if new_by_key[key].style != source_by_key[key].style:
            raise FrontMatterError(
                f"front_matter_translation_requires_style_change:{key}"
            )

    for key, value in source_fields.items():
        if key in updates:
            continue
        if new_fields.get(key) != value:
            raise FrontMatterError(
                f"front_matter_translation_requires_style_change:{key}"
            )

    source_selected = [(r.key, r.style) for r in source_records]
    new_selected = [(r.key, r.style) for r in new_records]
    if source_selected != new_selected:
        key = source_selected[0][0] if source_selected else "title"
        raise FrontMatterError(
            f"front_matter_translation_requires_style_change:{key}"
        )


def quoted_scalar_escapes(
    raw: str, record: FrontMatterValueRecord,
) -> tuple[tuple[int, int, str], ...]:
    """Source-owned escape spans and their text, never escapes invented by a model."""
    if record.style not in {"'", '"'}:
        return ()
    pattern = r"''" if record.style == "'" else r'\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[^\r\n])'
    start = record.start + 1
    result = []
    for match in re.finditer(pattern, raw[start:record.end - 1]):
        decoded = yaml.safe_load(record.style + match.group() + record.style)
        result.append((start + match.start(), start + match.end(), decoded))
    return tuple(result)


def encode_decoded_scalar(raw: str, record: FrontMatterValueRecord, value: str) -> str:
    """Serialize a decoded value in its owned span; untouched spans stay exact.

    Keep the original style when it represents the value exactly; otherwise
    use safe double quoting. No document-wide YAML dump is involved.
    """
    if value == record.value:
        return raw[record.start:record.end]
    try:
        updated = apply_front_matter_updates(raw, {record.key: value})
    except FrontMatterError:
        pass  # A scalar style change is serialization, not a model retry.
    else:
        suffix_length = len(raw) - record.end
        return updated[record.start:len(updated) - suffix_length if suffix_length else None]
    encoded = json.dumps(value, ensure_ascii=False)
    for char in set(re.findall(r"[\x7f-\x9f\u2028\u2029\ud800-\udfff\ufffe\uffff]", encoded)):
        encoded = encoded.replace(char, f"\\u{ord(char):04x}")
    if record.block_header_end is not None:
        header = raw[record.start:record.block_header_end]
        comment = header.find("#")
        if comment >= 0:
            encoded += " " + header[comment:].rstrip("\r\n")
        # A block scalar owns its final physical line ending as well.
        if raw[:record.end].endswith("\n"):
            encoded += record.newline or "\n"
    if yaml.safe_load(encoded) != value:
        raise FrontMatterError(f"front_matter_scalar_encoding_failed:{record.key}")
    return encoded
