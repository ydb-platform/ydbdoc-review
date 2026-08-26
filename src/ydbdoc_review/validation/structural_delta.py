"""Fail-closed three-way checks for localized structural documentation deltas."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from ydbdoc_review.parsing.ast_types import FencedCode, YfmInclude, YfmTabs
from ydbdoc_review.parsing.markdown_parser import parse_markdown

StructuralAtom = tuple[str, ...]


@dataclass(frozen=True)
class StructuralDeltaDecision:
    satisfied: bool
    reason: str
    additions: tuple[StructuralAtom, ...] = ()
    removals: tuple[StructuralAtom, ...] = ()
    fail_closed: bool = False


def _technical_title(title: list[object]) -> str:
    raw = "".join(str(getattr(node, "content", "")) for node in title)
    return re.sub(r"[^a-z0-9+#./_-]+", "", raw.casefold())


def _structural_atoms(text: str) -> list[StructuralAtom]:
    doc = parse_markdown(text)
    atoms: list[StructuralAtom] = []

    def walk(blocks: list[object], pane_path: tuple[str, ...]) -> None:
        for block in blocks:
            if isinstance(block, YfmTabs):
                atoms.append(("tabs", *pane_path, block.variant))
                occurrences: Counter[str] = Counter()
                for tab in block.children:
                    title = _technical_title(tab.title)
                    if not title:
                        continue
                    occurrences[title] += 1
                    identity = f"{title}@{occurrences[title]}"
                    child_path = (*pane_path, identity)
                    atoms.append(("pane", *child_path))
                    walk(list(tab.children), child_path)
                continue
            if isinstance(block, FencedCode):
                atoms.append(("fence", *pane_path, block.info.casefold().strip()))
            elif isinstance(block, YfmInclude):
                atoms.append(("include", *pane_path, block.path))
            children = getattr(block, "children", None)
            if isinstance(children, list):
                walk(children, pane_path)
            branches = getattr(block, "branches", None)
            if isinstance(branches, list):
                for branch in branches:
                    walk(list(branch.children), pane_path)

    walk(list(doc.children), ())
    return atoms


def _public(atom: StructuralAtom, title_counts: Counter[str]) -> StructuralAtom:
    parts = list(atom)
    for index, part in enumerate(parts[1:], start=1):
        if "@" not in part:
            continue
        title, occurrence = part.rsplit("@", 1)
        if occurrence == "1" and title_counts[title] == 1:
            parts[index] = title
    return tuple(parts)


def _is_subsequence(needles: list[StructuralAtom], haystack: list[StructuralAtom]) -> bool:
    cursor = 0
    for atom in needles:
        try:
            cursor = haystack.index(atom, cursor) + 1
        except ValueError:
            return False
    return True


def structural_delta_satisfied(
    source_before: str,
    source_after: str,
    localized_target: str,
) -> StructuralDeltaDecision:
    """Prove ordered pane/fence/include B→A operations already hold in T."""
    try:
        before = _structural_atoms(source_before)
        after = _structural_atoms(source_after)
        target = _structural_atoms(localized_target)
    except Exception as exc:
        return StructuralDeltaDecision(
            False, f"structural parse failed: {exc}", fail_closed=True
        )

    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    added: list[StructuralAtom] = []
    removed: list[StructuralAtom] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed.extend(before[i1:i2])
        if tag in {"replace", "insert"}:
            added.extend(after[j1:j2])
    if not added and not removed:
        return StructuralDeltaDecision(False, "no relevant structural delta")

    title_counts = Counter(
        atom[-1].split("@", 1)[0] for atom in after if atom[0] == "pane"
    )
    public_added = tuple(_public(atom, title_counts) for atom in added)
    public_removed = tuple(_public(atom, title_counts) for atom in removed)
    additions_present_in_order = _is_subsequence(added, target)
    stale = [atom for atom in removed if atom in target]
    if not additions_present_in_order or stale:
        later_structure = any(atom not in after for atom in target)
        return StructuralDeltaDecision(
            False,
            "unsatisfied ordered structural delta: "
            f"additions_in_order={additions_present_in_order}, stale={stale!r}",
            public_added,
            public_removed,
            bool(later_structure),
        )
    return StructuralDeltaDecision(
        True,
        "all ordered pane/fence/include operations already satisfied in localized target",
        public_added,
        public_removed,
    )
