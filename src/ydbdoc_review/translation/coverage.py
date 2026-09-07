"""Proof-based planning for conservative source coverage.

This module deliberately does not execute a plan. It records only coverage
that can be reconstructed from an exact checkpoint receipt or from an explicit
fragment obligation with unambiguous structural anchors.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.ops.translation_checkpoint import (
    CheckpointIdentity,
    VerifiedUnit,
    translation_unit_key,
    translation_unit_key_for_segment,
)
from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    Document,
    FencedCode,
    Heading,
    HTMLBlock,
    IndentedCode,
    OrderedList,
    Table,
    ThematicBreak,
    YfmCut,
    YfmIf,
    YfmInclude,
    YfmNote,
    YfmTabs,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import _render_inline_node
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import Segment

CoverageAction = Literal[
    "reuse_verified",
    "translate_required",
    "materialize_protected",
    "unresolved",
]
CoverageMode = Literal["full", "units"]

_SCHEMA_VERSION = 1
_ACTIONS = frozenset(
    {"reuse_verified", "translate_required", "materialize_protected", "unresolved"}
)
_MODES = frozenset({"full", "units"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ASCII_FRAGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
_HEADING_RE = re.compile(
    r"(?m)^(#{1,6})[ \t]+[^\r\n]*?"
    r"(?:[ \t]+\{#([^}\r\n]+)\})?[ \t]*(?:\r?\n|$)"
)


@dataclass(frozen=True)
class CoverageUnit:
    key: str
    action: CoverageAction
    source: str
    en_span: tuple[int, int] | None
    target: str | None
    reason: str


@dataclass(frozen=True)
class CoveragePlan:
    source_path: str
    source_hash: str
    en_hash: str | None
    units: tuple[CoverageUnit, ...]
    required_fragments: frozenset[str]
    mode: CoverageMode


@dataclass(frozen=True)
class _HeadingSpan:
    level: int
    anchor: str | None
    start: int
    section_end: int


def plan_source_coverage(
    *,
    source_path: str,
    source_text: str,
    existing_en: str | None,
    authority: RuAuthority,
    required_fragments: frozenset[str] = frozenset(),
    verified_units: tuple[VerifiedUnit, ...] = (),
    checkpoint_identity: CheckpointIdentity | None = None,
) -> CoveragePlan:
    """Plan exact source obligations without inferring historical EN coverage.

    The caller must supply the current expected checkpoint identity. An
    identity embedded in an otherwise valid receipt is not current authority.
    """
    normalized_path = source_path.replace("\\", "/")
    source_hash = _hash_text(source_text)
    en_hash = _hash_text(existing_en) if existing_en is not None else None

    if required_fragments:
        return _plan_required_fragments(
            source_path=normalized_path,
            source_text=source_text,
            existing_en=existing_en,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
        )

    try:
        source_doc = parse_markdown(source_text)
        source_segments = extract_segments(source_doc)
    except (AssertionError, TypeError, ValueError) as exc:
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
            reason=f"source parse failed; full translation fallback: {exc}",
            unresolved=True,
        )

    if not source_segments:
        if source_text:
            unit = CoverageUnit(
                key=translation_unit_key(
                    source=source_text.encode("utf-8"),
                    source_path=normalized_path,
                    target_locale="en",
                    atom_signature=(),
                    parent_context="coverage:protected-only",
                ),
                action="materialize_protected",
                source=source_text,
                en_span=None,
                target=source_text,
                reason="protected source structure",
            )
            return CoveragePlan(
                source_path=normalized_path,
                source_hash=source_hash,
                en_hash=en_hash,
                units=(unit,),
                required_fragments=required_fragments,
                mode="full",
            )
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
            reason="empty source cannot establish coverage",
            unresolved=True,
        )

    if existing_en is None:
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=None,
            required_fragments=required_fragments,
            reason="target does not exist; full translation required",
            segments=source_segments,
        )

    if checkpoint_identity is None:
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
            reason="current checkpoint identity not supplied; receipt reuse disabled",
            segments=source_segments,
        )
    if _SHA256_RE.fullmatch(checkpoint_identity.translation_fingerprint) is None:
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
            reason="current checkpoint translation fingerprint is invalid",
            segments=source_segments,
        )
    if checkpoint_identity.authority != authority:
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
            reason="current checkpoint authority does not match source authority",
            segments=source_segments,
        )

    try:
        en_doc = parse_markdown(existing_en)
        en_segments = extract_segments(en_doc)
    except (AssertionError, TypeError, ValueError) as exc:
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
            reason=f"existing EN parse failed; full translation fallback: {exc}",
            segments=source_segments,
        )

    if not _protected_structure_is_proven(source_doc, en_doc):
        return _full_fallback(
            source_path=normalized_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=en_hash,
            required_fragments=required_fragments,
            reason="protected source-to-EN structure is ambiguous or changed",
            segments=source_segments,
        )

    receipts_by_key: dict[str, list[VerifiedUnit]] = {}
    for verified in verified_units:
        receipts_by_key.setdefault(verified.receipt.unit_key, []).append(verified)

    reused: list[CoverageUnit] = []
    prior_end = -1
    for source_segment in source_segments:
        key = translation_unit_key_for_segment(
            source_segment,
            source_path=normalized_path,
            target_locale="en",
        )
        matching = receipts_by_key.get(key, [])
        if len(matching) != 1:
            return _full_fallback(
                source_path=normalized_path,
                source_text=source_text,
                source_hash=source_hash,
                en_hash=en_hash,
                required_fragments=required_fragments,
                reason="missing or ambiguous exact verified-unit receipt",
                segments=source_segments,
            )
        verified = matching[0]
        target_text = _verified_target(
            verified,
            expected_identity=checkpoint_identity,
            expected_key=key,
            source=source_segment.text.encode("utf-8"),
        )
        if target_text is None:
            return _full_fallback(
                source_path=normalized_path,
                source_text=source_text,
                source_hash=source_hash,
                en_hash=en_hash,
                required_fragments=required_fragments,
                reason="verified-unit receipt failed exact identity or hash validation",
                segments=source_segments,
            )
        candidates = [
            segment
            for segment in en_segments
            if segment.kind == source_segment.kind
            and segment.text == target_text
            and segment.heading_anchor == source_segment.heading_anchor
            and _atom_signature(segment) == _atom_signature(source_segment)
        ]
        if len(candidates) != 1:
            return _full_fallback(
                source_path=normalized_path,
                source_text=source_text,
                source_hash=source_hash,
                en_hash=en_hash,
                required_fragments=required_fragments,
                reason="current EN has no unique exact receipt target boundary",
                segments=source_segments,
            )
        raw_target = _materialize_protected_text(candidates[0])
        occurrences = _occurrences(existing_en, raw_target)
        if len(occurrences) != 1:
            return _full_fallback(
                source_path=normalized_path,
                source_text=source_text,
                source_hash=source_hash,
                en_hash=en_hash,
                required_fragments=required_fragments,
                reason="current EN target bytes do not identify one exact span",
                segments=source_segments,
            )
        start = occurrences[0]
        end = start + len(raw_target)
        if start < prior_end:
            return _full_fallback(
                source_path=normalized_path,
                source_text=source_text,
                source_hash=source_hash,
                en_hash=en_hash,
                required_fragments=required_fragments,
                reason="source-to-EN receipt boundaries are reordered or overlapping",
                segments=source_segments,
            )
        prior_end = end
        reused.append(
            CoverageUnit(
                key=key,
                action="reuse_verified",
                source=source_segment.text,
                en_span=(start, end),
                target=target_text,
                reason="matching validated source and EN receipt",
            )
        )

    return CoveragePlan(
        source_path=normalized_path,
        source_hash=source_hash,
        en_hash=en_hash,
        units=tuple(reused),
        required_fragments=required_fragments,
        mode="units",
    )


def _verified_target(
    verified: VerifiedUnit,
    *,
    expected_identity: CheckpointIdentity,
    expected_key: str,
    source: bytes,
) -> str | None:
    receipt = verified.receipt
    if (
        receipt.identity != expected_identity
        or receipt.validated is not True
        or receipt.unit_key != expected_key
        or verified.source != source
        or hashlib.sha256(source).hexdigest() != receipt.source_hash
        or hashlib.sha256(verified.target).hexdigest() != receipt.target_hash
        or receipt.object_key != f"translation/v1/objects/{receipt.target_hash}"
    ):
        return None
    try:
        return verified.target.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _atom_signature(segment: Segment) -> tuple[tuple[str, str], ...]:
    atoms: list[tuple[str, str]] = []
    for protected in segment.placeholders:
        node = protected.node
        payload = node.model_dump(mode="json") if hasattr(node, "model_dump") else str(node)
        kind = (
            str(payload.get("kind", type(node).__name__))
            if isinstance(payload, dict)
            else type(node).__name__
        )
        atoms.append(
            (
                kind,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    return tuple(atoms)


def _materialize_protected_text(segment: Segment) -> str:
    text = segment.text
    for protected in segment.placeholders:
        marker = protected.placeholder
        node = protected.node
        kind = getattr(node, "kind", "")
        if kind == "link":
            title = f' "{node.title}"' if node.title else ""
            text = text.replace(marker, "[", 1)
            text = text.replace(marker, f"]({node.href}{title})", 1)
        elif kind == "image":
            text = text.replace(marker, node.src, 1)
        else:
            text = text.replace(marker, _render_inline_node(node), 1)
    return text


def _protected_structure_is_proven(source: Document, target: Document) -> bool:
    source_atoms = _document_atoms(source)
    target_atoms = _document_atoms(target)
    for atom in set(source_atoms):
        if source_atoms.count(atom) != 1 or target_atoms.count(atom) != 1:
            return False
    return True


def _document_atoms(document: Document) -> tuple[tuple[str, str], ...]:
    atoms: list[tuple[str, str]] = []

    def add(kind: str, payload: object) -> None:
        atoms.append(
            (
                kind,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )

    def walk(blocks: list[BlockNode]) -> None:
        for block in blocks:
            if isinstance(block, Heading) and block.anchor is not None:
                add("heading_anchor", [block.level, block.anchor])
            elif isinstance(block, FencedCode):
                add("fenced_code", block.model_dump(mode="json"))
            elif isinstance(block, IndentedCode):
                add("indented_code", block.model_dump(mode="json"))
            elif isinstance(block, HTMLBlock):
                add("html_block", block.model_dump(mode="json"))
            elif isinstance(block, ThematicBreak):
                add("thematic_break", block.model_dump(mode="json"))
            elif isinstance(block, YfmInclude):
                add("yfm_include", [block.path, block.notitle])
            elif isinstance(block, (BlockQuote, YfmCut, YfmNote)):
                if isinstance(block, YfmNote):
                    add("yfm_note", block.note_type)
                walk(block.children)
            elif isinstance(block, YfmIf):
                for branch in block.branches:
                    add("yfm_if", branch.condition)
                    walk(branch.children)
            elif isinstance(block, YfmTabs):
                add("yfm_tabs", block.variant)
                for tab in block.children:
                    walk(tab.children)
            elif isinstance(block, (BulletList, OrderedList)):
                for item in block.children:
                    walk(item.children)
            elif isinstance(block, Table):
                add("table_shape", [len(block.header.cells), block.aligns])

    walk(document.children)
    return tuple(atoms)


def _occurrences(text: str, needle: str) -> list[int]:
    if not needle:
        return []
    found: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return found
        found.append(index)
        start = index + 1


def _heading_spans(text: str) -> tuple[_HeadingSpan, ...]:
    matches = list(_HEADING_RE.finditer(text))
    spans: list[_HeadingSpan] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        section_end = len(text)
        for following in matches[index + 1 :]:
            if len(following.group(1)) <= level:
                section_end = following.start()
                break
        spans.append(
            _HeadingSpan(
                level=level,
                anchor=match.group(2),
                start=match.start(),
                section_end=section_end,
            )
        )
    return tuple(spans)


def _plan_required_fragments(
    *,
    source_path: str,
    source_text: str,
    existing_en: str | None,
    source_hash: str,
    en_hash: str | None,
    required_fragments: frozenset[str],
) -> CoveragePlan:
    if existing_en is None:
        return _full_fallback(
            source_path=source_path,
            source_text=source_text,
            source_hash=source_hash,
            en_hash=None,
            required_fragments=required_fragments,
            reason="required fragment has no existing EN insertion context",
        )

    source_headings = _heading_spans(source_text)
    en_headings = _heading_spans(existing_en)
    units: list[CoverageUnit] = []
    for fragment in sorted(required_fragments):
        if _ASCII_FRAGMENT_RE.fullmatch(fragment) is None:
            return _fragment_fallback(
                source_path,
                source_text,
                source_hash,
                en_hash,
                required_fragments,
                f"ambiguous non-ASCII required fragment: {fragment}",
            )
        source_matches = [h for h in source_headings if h.anchor == fragment]
        if len(source_matches) != 1:
            return _fragment_fallback(
                source_path,
                source_text,
                source_hash,
                en_hash,
                required_fragments,
                f"ambiguous required fragment source section: {fragment}",
            )
        source_heading = source_matches[0]
        source_index = source_headings.index(source_heading)
        previous = next(
            (
                heading
                for heading in reversed(source_headings[:source_index])
                if heading.level <= source_heading.level
            ),
            None,
        )
        following = next(
            (
                heading
                for heading in source_headings[source_index + 1 :]
                if heading.level <= source_heading.level
            ),
            None,
        )
        if (
            previous is None
            or previous.anchor is None
            or following is None
            or following.anchor is None
            or sum(h.anchor == previous.anchor for h in source_headings) != 1
            or sum(h.anchor == following.anchor for h in source_headings) != 1
        ):
            return _fragment_fallback(
                source_path,
                source_text,
                source_hash,
                en_hash,
                required_fragments,
                f"ambiguous surrounding anchors for required fragment: {fragment}",
            )
        previous_en = [h for h in en_headings if h.anchor == previous.anchor]
        following_en = [h for h in en_headings if h.anchor == following.anchor]
        target_en = [h for h in en_headings if h.anchor == fragment]
        if len(previous_en) != 1 or len(following_en) != 1 or len(target_en) > 1:
            return _fragment_fallback(
                source_path,
                source_text,
                source_hash,
                en_hash,
                required_fragments,
                f"ambiguous current EN anchors for required fragment: {fragment}",
            )
        if target_en:
            target_heading = target_en[0]
            if not (
                previous_en[0].start < target_heading.start < following_en[0].start
                and target_heading.section_end == following_en[0].start
            ):
                return _fragment_fallback(
                    source_path,
                    source_text,
                    source_hash,
                    en_hash,
                    required_fragments,
                    f"ambiguous shifted EN section for required fragment: {fragment}",
                )
            en_span = (target_heading.start, target_heading.section_end)
        else:
            if previous_en[0].start >= following_en[0].start:
                return _fragment_fallback(
                    source_path,
                    source_text,
                    source_hash,
                    en_hash,
                    required_fragments,
                    f"ambiguous reordered EN insertion anchors for fragment: {fragment}",
                )
            en_span = (following_en[0].start, following_en[0].start)
        section = source_text[source_heading.start : source_heading.section_end]
        try:
            section_segments = extract_segments(parse_markdown(section))
        except (AssertionError, TypeError, ValueError):
            section_segments = []
        if not any(segment.kind.value != "heading" for segment in section_segments):
            return _fragment_fallback(
                source_path,
                source_text,
                source_hash,
                en_hash,
                required_fragments,
                f"required fragment section has no translatable definition: {fragment}",
            )
        units.append(
            CoverageUnit(
                key=translation_unit_key(
                    source=section.encode("utf-8"),
                    source_path=source_path,
                    target_locale="en",
                    atom_signature=(("heading_anchor", fragment),),
                    parent_context=json.dumps(
                        {
                            "following_anchor": following.anchor,
                            "fragment": fragment,
                            "previous_anchor": previous.anchor,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
                action="translate_required",
                source=section,
                en_span=en_span,
                target=None,
                reason=f"explicit required fragment section: {fragment}",
            )
        )

    candidate = CoveragePlan(
        source_path=source_path,
        source_hash=source_hash,
        en_hash=en_hash,
        units=tuple(units),
        required_fragments=required_fragments,
        mode="units",
    )
    try:
        _validate_plan(candidate)
    except ValueError as exc:
        return _fragment_fallback(
            source_path,
            source_text,
            source_hash,
            en_hash,
            required_fragments,
            f"ambiguous required fragment spans: {exc}",
        )
    return candidate


def _fragment_fallback(
    source_path: str,
    source_text: str,
    source_hash: str,
    en_hash: str | None,
    required_fragments: frozenset[str],
    reason: str,
) -> CoveragePlan:
    return _full_fallback(
        source_path=source_path,
        source_text=source_text,
        source_hash=source_hash,
        en_hash=en_hash,
        required_fragments=required_fragments,
        reason=reason,
    )


def _full_fallback(
    *,
    source_path: str,
    source_text: str,
    source_hash: str,
    en_hash: str | None,
    required_fragments: frozenset[str],
    reason: str,
    segments: list[Segment] | None = None,
    unresolved: bool = False,
) -> CoveragePlan:
    if segments is None:
        try:
            segments = extract_segments(parse_markdown(source_text))
        except (AssertionError, TypeError, ValueError):
            segments = []
    action: CoverageAction = "unresolved" if unresolved else "translate_required"
    if segments:
        units = tuple(
            CoverageUnit(
                key=translation_unit_key_for_segment(
                    segment,
                    source_path=source_path,
                    target_locale="en",
                ),
                action=action,
                source=segment.text,
                en_span=None,
                target=None,
                reason=reason,
            )
            for segment in segments
        )
    else:
        units = (
            CoverageUnit(
                key=translation_unit_key(
                    source=source_text.encode("utf-8"),
                    source_path=source_path,
                    target_locale="en",
                    atom_signature=(),
                    parent_context="coverage:full-fallback",
                ),
                action=action,
                source=source_text,
                en_span=None,
                target=None,
                reason=reason,
            ),
        )
    return CoveragePlan(
        source_path=source_path,
        source_hash=source_hash,
        en_hash=en_hash,
        units=units,
        required_fragments=required_fragments,
        mode="full",
    )


def encode_coverage_plan(plan: CoveragePlan) -> bytes:
    """Encode a validated plan as deterministic canonical JSON."""
    _validate_plan(plan)
    payload = {
        "en_hash": plan.en_hash,
        "mode": plan.mode,
        "required_fragments": sorted(plan.required_fragments),
        "schema": _SCHEMA_VERSION,
        "source_hash": plan.source_hash,
        "source_path": plan.source_path,
        "units": [
            {
                "action": unit.action,
                "en_span": list(unit.en_span) if unit.en_span is not None else None,
                "key": unit.key,
                "reason": unit.reason,
                "source": unit.source,
                "target": unit.target,
            }
            for unit in plan.units
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_coverage_plan(data: bytes) -> CoveragePlan:
    """Decode portable coverage evidence with a strict fail-closed schema."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed coverage plan JSON") from exc
    root = _strict_object(
        payload,
        field="plan",
        keys={
            "en_hash",
            "mode",
            "required_fragments",
            "schema",
            "source_hash",
            "source_path",
            "units",
        },
    )
    if type(root["schema"]) is not int or root["schema"] != _SCHEMA_VERSION:
        raise ValueError("unsupported coverage plan schema")
    raw_units = root["units"]
    if not isinstance(raw_units, list):
        raise ValueError("coverage plan units must be a list")
    units: list[CoverageUnit] = []
    for raw in raw_units:
        unit = _strict_object(
            raw,
            field="unit",
            keys={"action", "en_span", "key", "reason", "source", "target"},
        )
        raw_span = unit["en_span"]
        if raw_span is None:
            span = None
        elif (
            isinstance(raw_span, list)
            and len(raw_span) == 2
            and all(type(value) is int for value in raw_span)
        ):
            span = (raw_span[0], raw_span[1])
        else:
            raise ValueError("coverage unit span is malformed")
        units.append(
            CoverageUnit(
                key=_strict_string(unit["key"], field="unit.key"),
                action=_strict_string(unit["action"], field="unit.action"),  # type: ignore[arg-type]
                source=_strict_string(unit["source"], field="unit.source"),
                en_span=span,
                target=(
                    None
                    if unit["target"] is None
                    else _strict_string(unit["target"], field="unit.target")
                ),
                reason=_strict_string(unit["reason"], field="unit.reason"),
            )
        )
    raw_fragments = root["required_fragments"]
    if not isinstance(raw_fragments, list) or not all(type(x) is str for x in raw_fragments):
        raise ValueError("coverage plan required_fragments must be a string list")
    if raw_fragments != sorted(set(raw_fragments)):
        raise ValueError("coverage plan required_fragments are not canonical")
    raw_en_hash = root["en_hash"]
    plan = CoveragePlan(
        source_path=_strict_string(root["source_path"], field="source_path"),
        source_hash=_strict_string(root["source_hash"], field="source_hash"),
        en_hash=None if raw_en_hash is None else _strict_string(raw_en_hash, field="en_hash"),
        units=tuple(units),
        required_fragments=frozenset(raw_fragments),
        mode=_strict_string(root["mode"], field="mode"),  # type: ignore[arg-type]
    )
    _validate_plan(plan)
    return plan


def _validate_plan(plan: CoveragePlan) -> None:
    if type(plan.source_path) is not str or not plan.source_path or "\\" in plan.source_path:
        raise ValueError("coverage plan source_path is malformed")
    _require_hash(plan.source_hash, field="source_hash")
    if plan.en_hash is not None:
        _require_hash(plan.en_hash, field="en_hash")
    if type(plan.mode) is not str or plan.mode not in _MODES:
        raise ValueError("coverage plan mode is invalid")
    if plan.mode == "units" and plan.en_hash is None:
        raise ValueError("units coverage plan requires en_hash")
    if type(plan.units) is not tuple or not plan.units:
        raise ValueError("coverage plan units must be a non-empty tuple")
    if type(plan.required_fragments) is not frozenset or not all(
        type(fragment) is str and fragment for fragment in plan.required_fragments
    ):
        raise ValueError("coverage plan required_fragments are malformed")

    keys: set[str] = set()
    spans: list[tuple[int, int]] = []
    zero_offsets: set[int] = set()
    for unit in plan.units:
        if not isinstance(unit, CoverageUnit):
            raise ValueError("coverage plan unit is malformed")
        _require_hash(unit.key, field="unit.key")
        if unit.key in keys:
            raise ValueError("coverage plan unit key is duplicated")
        keys.add(unit.key)
        if type(unit.action) is not str or unit.action not in _ACTIONS:
            raise ValueError("coverage unit action is invalid")
        if type(unit.source) is not str or type(unit.reason) is not str or not unit.reason:
            raise ValueError("coverage unit source/reason is malformed")
        if unit.action in {"reuse_verified", "materialize_protected"}:
            if type(unit.target) is not str:
                raise ValueError("covered unit target is missing")
        elif unit.target is not None:
            raise ValueError("pending coverage unit target must be null")
        if unit.en_span is None:
            if plan.mode == "units":
                raise ValueError("units coverage plan requires an EN span")
            continue
        if plan.mode == "full":
            raise ValueError("full coverage plan cannot contain an EN span")
        if (
            type(unit.en_span) is not tuple
            or len(unit.en_span) != 2
            or any(type(value) is not int for value in unit.en_span)
        ):
            raise ValueError("coverage unit span is malformed")
        start, end = unit.en_span
        if start < 0 or end < start:
            raise ValueError("coverage unit span is invalid")
        if plan.en_hash is None:
            raise ValueError("coverage unit span requires en_hash")
        if start == end:
            if start in zero_offsets:
                raise ValueError("coverage unit spans overlap at insertion boundary")
            zero_offsets.add(start)
        spans.append((start, end))

    previous_end = -1
    for start, end in sorted(spans):
        if start < previous_end:
            raise ValueError("coverage unit spans overlap")
        previous_end = max(previous_end, end)


def _strict_object(value: object, *, field: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"malformed coverage plan {field}")
    return value


def _strict_string(value: object, *, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"coverage plan {field} must be a string")
    return value


def _require_hash(value: object, *, field: str) -> str:
    text = _strict_string(value, field=field)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"coverage plan {field} has invalid hash")
    return text


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
