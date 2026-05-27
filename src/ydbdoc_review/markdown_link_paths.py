"""Relative markdown link path helpers for RU↔EN parity checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ydbdoc_review.markdown_links import _LINK_RE

_YANDEX_DOCS_LOCALE_RE = re.compile(
    r"^(.*?)(/ru/docs/|/en/docs/)(.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RelativeLinkRef:
    """Normalized relative link for comparison."""

    depth: int
    suffix: str
    raw: str


def _is_external_or_anchor(href: str) -> bool:
    h = href.strip()
    return (
        not h
        or h.startswith("#")
        or h.startswith("http://")
        or h.startswith("https://")
        or h.startswith("mailto:")
    )


def normalize_relative_href(href: str) -> RelativeLinkRef | None:
    """Map ``../../../../foo/bar.md`` → depth + suffix; normalize Yandex Cloud locale."""
    raw = href.strip()
    if _is_external_or_anchor(raw):
        return None
    path = raw.split("#", 1)[0]
    depth = 0
    rest = path
    while rest.startswith("../"):
        depth += 1
        rest = rest[3:]
    norm = rest
    m = _YANDEX_DOCS_LOCALE_RE.match(rest)
    if m:
        norm = f"{m.group(1)}/{{locale}}/docs/{m.group(3)}"
    return RelativeLinkRef(depth=depth, suffix=norm, raw=raw)


def extract_relative_link_refs(text: str) -> list[RelativeLinkRef]:
    refs: list[RelativeLinkRef] = []
    for _text, href in _LINK_RE.findall(text):
        ref = normalize_relative_href(href)
        if ref is not None:
            refs.append(ref)
    return refs


def _ref_key(ref: RelativeLinkRef) -> tuple[int, str]:
    return (ref.depth, ref.suffix)


def missing_relative_link_details(
    source: str, translation: str, *, limit: int = 5
) -> list[str]:
    """
    Human-readable RU links present in SOURCE but missing from TRANSLATION.

    Uses normalized ``(depth, suffix)`` keys so locale swaps in Yandex URLs do not
    count as missing when the EN link exists.
    """
    from collections import Counter

    trn_counts: Counter[tuple[int, str]] = Counter(
        _ref_key(r) for r in extract_relative_link_refs(translation)
    )
    out: list[str] = []
    for ru_text, href in _LINK_RE.findall(source):
        ref = normalize_relative_href(href)
        if ref is None:
            continue
        key = _ref_key(ref)
        if trn_counts.get(key, 0) > 0:
            trn_counts[key] -= 1
            continue
        label = re.sub(r"\s+", " ", ru_text.strip())[:70]
        out.append(f"[{label}]({ref.raw})")
        if len(out) >= limit:
            break
    return out


def extra_relative_link_details(
    translation: str, source: str, *, limit: int = 3
) -> list[str]:
    """EN relative links with no matching RU link (normalized key)."""
    from collections import Counter

    src_counts: Counter[tuple[int, str]] = Counter(
        _ref_key(r) for r in extract_relative_link_refs(source)
    )
    out: list[str] = []
    for en_text, href in _LINK_RE.findall(translation):
        ref = normalize_relative_href(href)
        if ref is None:
            continue
        key = _ref_key(ref)
        if src_counts.get(key, 0) > 0:
            src_counts[key] -= 1
            continue
        label = re.sub(r"\s+", " ", en_text.strip())[:70]
        out.append(f"[{label}]({ref.raw})")
        if len(out) >= limit:
            break
    return out
