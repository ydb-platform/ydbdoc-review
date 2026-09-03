"""YFM heading anchors: parse Cyrillic ids and emit English anchors for EN docs."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import unquote

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    Document,
    Heading,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTabs,
)

_HEADING_ANCHOR_SUFFIX = re.compile(r"\s*\{#([^}]+)\}\s*$")
_RU_TRANSLITERATION = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def split_heading_anchor_suffix(text: str) -> tuple[str, str | None]:
    """Split trailing ``{#anchor}`` from heading inline text."""
    match = _HEADING_ANCHOR_SUFFIX.search(text)
    if not match:
        return text, None
    return text[: match.start()].rstrip(), match.group(1)


def diplodoc_auto_slug(text: str) -> str:
    """Diplodoc-style auto anchor from visible heading text."""
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug


def _legacy_transliterated_slug(text: str) -> str:
    """Return the ASCII slug used by older RU documentation links."""
    return diplodoc_auto_slug(text.lower().translate(_RU_TRANSLITERATION))


def is_ascii_yfm_anchor(anchor: str | None) -> bool:
    """True when §8 requires the EN explicit id to match RU byte-for-byte."""
    return bool(anchor) and anchor.isascii() and bool(re.fullmatch(r"[A-Za-z0-9_\-.]+", anchor))


def english_yfm_anchor(ru_anchor: str | None, english_heading: str) -> str | None:
    """Map a RU/Cyrillic YFM anchor to an English id for EN output.

    Examples: ``fields-Описание`` + "Description of fields…" → ``fields-Description``.
    ASCII anchors are returned unchanged.
    """
    if not ru_anchor:
        return None
    if is_ascii_yfm_anchor(ru_anchor):
        return ru_anchor

    prefix, sep, suffix = ru_anchor.partition("-")
    if sep and suffix and not suffix.isascii():
        word = re.match(r"([A-Za-z][A-Za-z0-9]*)", english_heading.strip())
        if word:
            return f"{prefix}-{word.group(1)}"

    slug = diplodoc_auto_slug(english_heading)
    if slug and is_ascii_yfm_anchor(slug):
        return slug
    legacy = _legacy_transliterated_slug(ru_anchor)
    if legacy and is_ascii_yfm_anchor(legacy):
        return legacy
    return slug or ru_anchor


@dataclass
class JobAnchorDictionary:
    """Job-scoped Cyrillic RU → EN explicit-anchor map (REQUIREMENTS §8).

    ASCII RU anchors are never stored: callers must keep them byte-identical.
    Re-resolving the same Cyrillic RU key always returns the first EN value.
    """

    _ru_to_en: dict[str, str] = field(default_factory=dict)

    def as_map(self) -> dict[str, str]:
        return dict(self._ru_to_en)

    def get(self, ru_anchor: str) -> str | None:
        if is_ascii_yfm_anchor(ru_anchor):
            return ru_anchor
        return self._ru_to_en.get(ru_anchor)

    def __contains__(self, ru_anchor: object) -> bool:
        return isinstance(ru_anchor, str) and ru_anchor in self._ru_to_en

    def lookup_or_insert(self, ru_anchor: str, english_heading: str = "") -> str:
        """Return the EN id for ``ru_anchor``, minting once for Cyrillic keys."""
        if not ru_anchor:
            return ru_anchor
        if is_ascii_yfm_anchor(ru_anchor):
            return ru_anchor
        existing = self._ru_to_en.get(ru_anchor)
        if existing is not None:
            return existing
        minted = english_yfm_anchor(ru_anchor, english_heading) or ""
        if not is_ascii_yfm_anchor(minted):
            minted = _legacy_transliterated_slug(ru_anchor) or "anchor"
        owned = set(self._ru_to_en.values())
        if minted in owned:
            base = minted
            n = 2
            while f"{base}-{n}" in owned:
                n += 1
            minted = f"{base}-{n}"
        self._ru_to_en[ru_anchor] = minted
        return minted


def apply_job_anchors_to_document(
    target_doc: Document,
    *,
    dictionary: JobAnchorDictionary,
    source_doc: Document | None = None,
) -> None:
    """Rewrite Cyrillic explicit heading anchors on *target_doc* via *dictionary*."""
    target_heads = list(_iter_headings(target_doc.children))
    source_heads = list(_iter_headings(source_doc.children)) if source_doc is not None else []
    for index, tgt_h in enumerate(target_heads):
        ru_anchor = None
        if index < len(source_heads) and source_heads[index].anchor:
            ru_anchor = source_heads[index].anchor
        elif tgt_h.anchor:
            ru_anchor = tgt_h.anchor
        if not ru_anchor:
            continue
        en_text = _heading_plain_text(tgt_h)
        tgt_h.anchor = dictionary.lookup_or_insert(ru_anchor, en_text)


def _heading_plain_text(heading: Heading) -> str:
    from ydbdoc_review.rendering.markdown_renderer import _render_inline

    return _render_inline(heading.children).strip()


def _iter_headings(blocks: list[BlockNode]) -> Iterator[Heading]:
    for block in blocks:
        if isinstance(block, Heading):
            yield block
        elif isinstance(block, (BulletList, OrderedList)):
            for item in block.children:
                if isinstance(item, ListItem):
                    yield from _iter_headings(item.children)
        elif isinstance(block, BlockQuote):
            yield from _iter_headings(block.children)
        elif isinstance(block, Table):
            for cell in block.header.cells:
                yield from _iter_headings([Paragraph(children=cell.children)])
            for row in block.rows:
                for cell in row.cells:
                    yield from _iter_headings([Paragraph(children=cell.children)])
        elif isinstance(block, YfmNote):
            yield from _iter_headings(block.children)
        elif isinstance(block, YfmTabs):
            for tab in block.children:
                yield from _iter_headings(tab.children)
        elif isinstance(block, YfmCut):
            yield from _iter_headings(block.children)
        elif isinstance(block, YfmIf):
            for branch in block.branches:
                yield from _iter_headings(branch.children)


def build_heading_anchor_map(source: Document, target: Document) -> dict[str, str]:
    """Map RU heading slugs/explicit anchors to EN counterparts."""
    mapping: dict[str, str] = {}
    source_headings = list(_iter_headings(source.children))
    target_headings = list(_iter_headings(target.children))
    # Positional pairing is safe only while the translated outline is aligned.
    # Otherwise a valid but unrelated EN anchor is worse than leaving the
    # fragment for the outbound validator to reject.
    if len(source_headings) != len(target_headings) or any(
        src.level != tgt.level for src, tgt in zip(source_headings, target_headings, strict=True)
    ):
        return mapping
    for src_h, tgt_h in zip(source_headings, target_headings, strict=True):
        ru_text = _heading_plain_text(src_h)
        en_text = _heading_plain_text(tgt_h)
        ru_auto = diplodoc_auto_slug(ru_text)
        en_auto = diplodoc_auto_slug(en_text)
        if ru_auto and en_auto and ru_auto != en_auto:
            mapping[ru_auto] = en_auto
            legacy_ru_auto = _legacy_transliterated_slug(ru_text)
            if legacy_ru_auto and legacy_ru_auto != ru_auto:
                mapping[legacy_ru_auto] = en_auto
        if src_h.anchor:
            if tgt_h.anchor and is_ascii_yfm_anchor(tgt_h.anchor):
                en_explicit = tgt_h.anchor
            else:
                en_explicit = english_yfm_anchor(src_h.anchor, en_text) or en_auto
            if en_explicit:
                mapping[src_h.anchor] = en_explicit
                decoded = unquote(src_h.anchor)
                if decoded != src_h.anchor:
                    mapping[decoded] = en_explicit
                # RU links often use Diplodoc auto-slugs while the heading keeps
                # an explicit ``{#id}`` (e.g. ``#информация-…-users`` → ``#users``).
                if ru_auto and en_explicit:
                    mapping[ru_auto] = en_explicit
    return mapping
