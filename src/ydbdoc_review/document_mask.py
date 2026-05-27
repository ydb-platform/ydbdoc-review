"""Inline mask/unmask: replace non-translatable spans with ``⟦KIND:n⟧`` placeholders."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Unicode corner brackets — unlikely in docs or model output.
PLACEHOLDER_RE = re.compile(r"⟦([A-Z][A-Z0-9_]*:\d+)⟧")

_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_VAR_RE = re.compile(r"\{\{[^}]+\}\}")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_HTML_TAG_RE = re.compile(r"<[^>\n]+>")
_ANCHOR_RE = re.compile(r"\{#[^}]+\}")
_DIPLODOC_INLINE_RE = re.compile(
    r"\{%\s*(?:note|cut|endnote|endcut|list\s+tabs|endlist)[^%]*%\}",
    re.IGNORECASE,
)
_FENCE_BLOCK_RE = re.compile(r"```[^\n]*\n[\s\S]*?```", re.MULTILINE)


@dataclass
class MaskRegistry:
    """Maps placeholder keys (``LINK:1``) to original source spans."""

    atoms: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def reserve(self, kind: str, original: str) -> str:
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        key = f"{kind}:{n}"
        self.atoms[key] = original
        return f"⟦{key}⟧"

    def placeholder_keys_in_text(self, text: str) -> list[str]:
        return PLACEHOLDER_RE.findall(text)

    def copy(self) -> MaskRegistry:
        other = MaskRegistry()
        other.atoms = dict(self.atoms)
        other._counters = dict(self._counters)
        return other


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str
    original: str


def _collect_spans(text: str, *, include_fences: bool) -> list[_Span]:
    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("FENCE", _FENCE_BLOCK_RE),
        ("DIPL", _DIPLODOC_INLINE_RE),
        ("LINK", _LINK_RE),
        ("VAR", _VAR_RE),
        ("CODE", _INLINE_CODE_RE),
        ("HTML", _HTML_TAG_RE),
        ("ANCHOR", _ANCHOR_RE),
    ]
    if not include_fences:
        patterns = [p for p in patterns if p[0] != "FENCE"]

    spans: list[_Span] = []
    for kind, pat in patterns:
        for m in pat.finditer(text):
            spans.append(_Span(m.start(), m.end(), kind, m.group(0)))
    spans.sort(key=lambda s: (s.start, -s.end))
    return _dedupe_spans(spans)


def _dedupe_spans(spans: list[_Span]) -> list[_Span]:
    if not spans:
        return []
    kept: list[_Span] = []
    last_end = -1
    for sp in spans:
        if sp.start < last_end:
            continue
        kept.append(sp)
        last_end = sp.end
    return kept


def mask_translatable_text(
    text: str,
    registry: MaskRegistry,
    *,
    include_fences: bool = False,
) -> str:
    """Replace links, HTML, vars, code, diplodoc directives with placeholders."""
    spans = _collect_spans(text, include_fences=include_fences)
    if not spans:
        return text
    out: list[str] = []
    pos = 0
    for sp in spans:
        out.append(text[pos : sp.start])
        out.append(registry.reserve(sp.kind, sp.original))
        pos = sp.end
    out.append(text[pos:])
    return "".join(out)


def unmask_text(text: str, registry: MaskRegistry) -> str:
    """Restore original spans from placeholders."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return registry.atoms.get(key, match.group(0))

    return PLACEHOLDER_RE.sub(repl, text)


def placeholder_key_sequence(text: str) -> list[str]:
    return PLACEHOLDER_RE.findall(text)


def restore_missing_placeholders(source_masked: str, translated: str) -> str:
    """
    Re-insert placeholders dropped or corrupted by the model.

    Walks *source_masked* and *translated* in parallel by placeholder keys; prose
    between placeholders is taken from *translated* when structure aligns.
    """
    src_parts = _split_by_placeholders(source_masked)
    tr_parts = _split_by_placeholders(translated)
    if len(src_parts) == len(tr_parts):
        out: list[str] = []
        for src_p, tr_p in zip(src_parts, tr_parts, strict=False):
            if src_p.kind == "ph":
                out.append(src_p.text)
            else:
                out.append(tr_p.text if tr_p.kind == "prose" else src_p.text)
        merged = "".join(out)
        if not validate_placeholders(source_masked, merged):
            return merged
        return _rebuild_from_source_keys(source_masked, translated)
    return _rebuild_from_source_keys(source_masked, translated)


@dataclass(frozen=True)
class _Part:
    kind: str  # prose | ph
    text: str


def _split_by_placeholders(text: str) -> list[_Part]:
    parts: list[_Part] = []
    last = 0
    for m in PLACEHOLDER_RE.finditer(text):
        if m.start() > last:
            parts.append(_Part("prose", text[last : m.start()]))
        parts.append(_Part("ph", m.group(0)))
        last = m.end()
    if last < len(text):
        parts.append(_Part("prose", text[last:]))
    return parts


def _rebuild_from_source_keys(source_masked: str, translated: str) -> str:
    """If structure diverged, interleave translated prose with source placeholders."""
    keys = placeholder_key_sequence(source_masked)
    if not keys:
        return translated
    src_parts = _split_by_placeholders(source_masked)
    tr_prose = [p.text for p in _split_by_placeholders(translated) if p.kind == "prose"]
    out: list[str] = []
    prose_i = 0
    for part in src_parts:
        if part.kind == "ph":
            out.append(part.text)
        elif prose_i < len(tr_prose):
            out.append(tr_prose[prose_i])
            prose_i += 1
        else:
            out.append(part.text)
    merged = "".join(out)
    for key in keys:
        token = f"⟦{key}⟧"
        if token not in merged:
            corrupt = re.compile(
                rf"\[\[?{re.escape(key)}\]?]|\[\[?{re.escape(key.split(':')[0])}[^\]]*\]\]",
                re.IGNORECASE,
            )
            if corrupt.search(merged):
                merged = corrupt.sub(token, merged, count=1)
    return merged


def validate_placeholders(source_masked: str, translated: str) -> list[str]:
    """Return list of placeholder keys present in source but missing in translation."""
    src = placeholder_key_sequence(source_masked)
    tr_set = set(placeholder_key_sequence(translated))
    return [k for k in src if k not in tr_set]
