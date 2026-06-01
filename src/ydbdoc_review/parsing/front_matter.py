"""YAML front matter parse / serialize for translatable keys."""

from __future__ import annotations

import re
from typing import Any

import yaml

TRANSLATABLE_FRONT_MATTER_KEYS: tuple[str, ...] = ("title", "description")

_KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:", re.MULTILINE)


def parse_front_matter(raw: str) -> dict[str, Any]:
    """Parse YAML front matter body (without ``---`` delimiters)."""
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        return {}
    return dict(data)


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
    fields = parse_front_matter(raw)
    order = front_matter_key_order(raw)
    for key, value in updates.items():
        if key in TRANSLATABLE_FRONT_MATTER_KEYS:
            fields[key] = value
    return dump_front_matter(fields, key_order=order)


def translatable_front_matter_fields(raw: str) -> dict[str, str]:
    """Return non-empty string values for translatable keys."""
    fields = parse_front_matter(raw)
    out: dict[str, str] = {}
    for key in TRANSLATABLE_FRONT_MATTER_KEYS:
        val = fields.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val
    return out
