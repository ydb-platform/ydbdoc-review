"""Fail-closed three-way checks for localized structural documentation deltas."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from ydbdoc_review.parsing.ast_types import FencedCode, YfmInclude, YfmTabs
from ydbdoc_review.parsing.markdown_parser import parse_markdown

StructuralAtom = tuple[str, ...]


class HistoricalDeltaStatus(StrEnum):
    ALREADY_TRANSLATED = "already_translated"
    SUPERSEDED = "superseded"
    MISSING_CURRENT_DELTA = "missing_current_delta"
    TRANSLATED_NOW = "translated_now"
    AMBIGUOUS = "ambiguous"
    NO_RELEVANT_DELTA = "no_relevant_delta"


@dataclass(frozen=True)
class StructuralDeltaDecision:
    satisfied: bool
    reason: str
    additions: tuple[StructuralAtom, ...] = ()
    removals: tuple[StructuralAtom, ...] = ()
    fail_closed: bool = False
    status: HistoricalDeltaStatus = HistoricalDeltaStatus.AMBIGUOUS


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
    *,
    current_source: str | None = None,
) -> StructuralDeltaDecision:
    """Classify surviving historical B→A operations against current RU/EN.

    Historical jobs own only operations which still survive in current RU.
    Operations removed or replaced by later RU changes are superseded.  Later
    RU/EN structure outside those operations is deliberately out of scope.
    """
    try:
        before = _structural_atoms(source_before)
        after = _structural_atoms(source_after)
        target = _structural_atoms(localized_target)
        current = _structural_atoms(current_source or source_after)
    except Exception as exc:
        return StructuralDeltaDecision(
            False,
            f"structural parse failed: {exc}",
            fail_closed=True,
            status=HistoricalDeltaStatus.AMBIGUOUS,
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
        return StructuralDeltaDecision(
            False,
            "no relevant structural delta",
            status=HistoricalDeltaStatus.NO_RELEVANT_DELTA,
        )

    title_counts = Counter(
        atom[-1].split("@", 1)[0] for atom in after if atom[0] == "pane"
    )
    public_added = tuple(_public(atom, title_counts) for atom in added)
    public_removed = tuple(_public(atom, title_counts) for atom in removed)
    before_counts = Counter(before)
    after_counts = Counter(after)
    current_counts = Counter(current)
    target_counts = Counter(target)

    net_added = Counter(
        {atom: count - before_counts[atom] for atom, count in after_counts.items() if count > before_counts[atom]}
    )
    net_removed = Counter(
        {atom: count - after_counts[atom] for atom, count in before_counts.items() if count > after_counts[atom]}
    )

    surviving_additions = Counter(
        {
            atom: count
            for atom, count in net_added.items()
            if current_counts[atom] >= before_counts[atom] + count
        }
    )
    surviving_removals = Counter(
        {
            atom: count
            for atom, count in net_removed.items()
            if current_counts[atom] <= after_counts[atom]
        }
    )
    if not surviving_additions and not surviving_removals:
        return StructuralDeltaDecision(
            True,
            "all historical structural operations were superseded in current RU",
            public_added,
            public_removed,
            status=HistoricalDeltaStatus.SUPERSEDED,
        )

    missing_additions = [
        atom
        for atom, count in surviving_additions.items()
        if target_counts[atom] < before_counts[atom] + count
    ]
    stale_removals = [
        atom
        for atom in surviving_removals
        if target_counts[atom] > after_counts[atom]
    ]
    ordered_surviving = [atom for atom in added if surviving_additions[atom]]
    additions_in_current_order = _is_subsequence(ordered_surviving, current)
    additions_in_target_order = _is_subsequence(ordered_surviving, target)
    if missing_additions or stale_removals or not additions_in_target_order:
        needs_structural_rebuild = any(
            atom[0] in {"tabs", "pane", "fence"} for atom in missing_additions
        )
        return StructuralDeltaDecision(
            False,
            "surviving historical operations are missing from current EN: "
            f"missing={missing_additions!r}, stale={stale_removals!r}, "
            f"current_order={additions_in_current_order}, target_order={additions_in_target_order}",
            public_added,
            public_removed,
            needs_structural_rebuild,
            HistoricalDeltaStatus.MISSING_CURRENT_DELTA,
        )
    return StructuralDeltaDecision(
        True,
        "all surviving historical operations already translated in current EN",
        public_added,
        public_removed,
        status=HistoricalDeltaStatus.ALREADY_TRANSLATED,
    )
