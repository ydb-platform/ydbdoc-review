"""Typed, non-mutating whole-file validation before documentation writes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ydbdoc_review.parsing.ast_types import FencedCode, Heading, YfmInclude, YfmTabs
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.homoglyphs import normalize_confusable_cyrillic

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_PLACEHOLDER = re.compile(r"⟦[^⟧]+⟧|__YDBDOC_[A-Z0-9_]+__")


class HardValidationCode(str, Enum):
    PARSE = "parse"
    FENCE_STRUCTURE = "fence_structure"
    PANE_TREE = "pane_tree"
    HEADING_STRUCTURE = "heading_structure"
    ANCHORS = "anchors"
    INCLUDE_TOPOLOGY = "include_topology"
    PLACEHOLDER_RESIDUE = "placeholder_residue"
    CYRILLIC_TECHNICAL_TITLE = "cyrillic_technical_title"


@dataclass(frozen=True)
class HardValidationError:
    path: str
    code: HardValidationCode
    detail: str


def _inline_text(nodes: list[object]) -> str:
    return "".join(str(getattr(node, "content", "")) for node in nodes).strip()


def _shape(text: str) -> dict[str, list[tuple[str, ...]]]:
    doc = parse_markdown(text)
    out: dict[str, list[tuple[str, ...]]] = {
        "fences": [], "panes": [], "headings": [], "anchors": [], "includes": []
    }

    def walk(blocks: list[object], panes: tuple[str, ...]) -> None:
        for block in blocks:
            if isinstance(block, Heading):
                out["headings"].append((str(block.level),))
                if block.anchor:
                    out["anchors"].append((block.anchor,))
            if isinstance(block, YfmTabs):
                sibling_counts: dict[str, int] = {}
                for tab in block.children:
                    raw = _inline_text(tab.title)
                    normalized = normalize_confusable_cyrillic(raw).casefold()
                    sibling_counts[normalized] = sibling_counts.get(normalized, 0) + 1
                    identity = f"{normalized}@{sibling_counts[normalized]}"
                    path = (*panes, identity)
                    out["panes"].append(path)
                    if _CYRILLIC.search(raw):
                        out.setdefault("cyrillic_titles", []).append((raw,))
                    walk(list(tab.children), path)
                continue
            if isinstance(block, FencedCode):
                out["fences"].append((*panes, block.info.casefold().strip()))
            elif isinstance(block, YfmInclude):
                out["includes"].append((*panes, block.path))
            children = getattr(block, "children", None)
            if isinstance(children, list):
                walk(children, panes)
            branches = getattr(block, "branches", None)
            if isinstance(branches, list):
                for branch in branches:
                    walk(list(branch.children), panes)

    walk(list(doc.children), ())
    return out


def validate_whole_file(
    *, path: str, authoritative_ru: str, candidate_en: str
) -> list[HardValidationError]:
    """Return hard structural errors; never repair or mutate either document."""
    try:
        ru = _shape(authoritative_ru)
        en = _shape(candidate_en)
    except Exception as exc:
        return [HardValidationError(path, HardValidationCode.PARSE, str(exc))]
    errors: list[HardValidationError] = []
    checks = (
        ("fences", HardValidationCode.FENCE_STRUCTURE),
        ("panes", HardValidationCode.PANE_TREE),
        ("headings", HardValidationCode.HEADING_STRUCTURE),
        ("anchors", HardValidationCode.ANCHORS),
        ("includes", HardValidationCode.INCLUDE_TOPOLOGY),
    )
    for key, code in checks:
        if ru[key] != en[key]:
            errors.append(HardValidationError(path, code, f"RU={ru[key]!r}; EN={en[key]!r}"))
    if _PLACEHOLDER.search(candidate_en):
        errors.append(
            HardValidationError(
                path, HardValidationCode.PLACEHOLDER_RESIDUE, "protect marker remains"
            )
        )
    if en.get("cyrillic_titles"):
        errors.append(
            HardValidationError(
                path,
                HardValidationCode.CYRILLIC_TECHNICAL_TITLE,
                repr(en["cyrillic_titles"]),
            )
        )
    return errors
