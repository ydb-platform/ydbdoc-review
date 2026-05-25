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
