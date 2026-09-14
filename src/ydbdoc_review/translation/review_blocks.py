"""Immutable, lossless preparation evidence for final-candidate semantic review.

Document pairing is explicit authority supplied by the caller. Parser-owned
container structure and enclosing headings establish local correspondence.
Indistinguishable neighboring blocks may be grouped inside a proven section;
ambiguous sections remain incomplete. Context includes only enclosing headings.
This manifest proves input delivery, not model completion or verdict quality.
"""

import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from hashlib import sha256

from ydbdoc_review.parsing.inline_locations import SourceSpan
from ydbdoc_review.parsing.markdown_parser import parse_review_blocks
from ydbdoc_review.pipeline.final_candidate import FinalCandidate, read_candidate_bytes


@dataclass(frozen=True)
class AuthoritativeDocument:
    path: str
    source_id: str
    content: bytes


@dataclass(frozen=True)
class ReviewBlock:
    id: str
    path: str
    snapshot_id: str
    content_sha256: str
    kind: str
    address: tuple[int, ...]
    span: SourceSpan
    line_start: int
    line_end: int
    text: str
    structure: tuple[str, ...] = ()
    heading_level: int = 0
    anchor: str | None = None


@dataclass(frozen=True)
class ReviewIssue:
    candidate_sha: str
    path: str
    span: SourceSpan
    reason: str
    en_block_ids: tuple[str, ...] = ()
    ru_block_ids: tuple[str, ...] = ()
    code: str = "incomplete-review"


@dataclass(frozen=True)
class BlockInventory:
    path: str
    snapshot_id: str
    content_sha256: str
    text: str
    blocks: tuple[ReviewBlock, ...]
    empty_reason: str | None = None
    candidate_sha: str = ""
    candidate_tree_sha: str = ""


@dataclass(frozen=True)
class ReviewUnit:
    id: str
    candidate_sha: str
    candidate_tree_sha: str
    ru_path: str
    en_path: str
    source_id: str
    ru_block_ids: tuple[str, ...]
    en_block_ids: tuple[str, ...]
    ru_text: str
    en_text: str
    alignment: str = "authoritative-file-pair"
    context: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewPlan:
    candidate: FinalCandidate
    ru: BlockInventory
    en: BlockInventory
    units: tuple[ReviewUnit, ...]
    issues: tuple[ReviewIssue, ...]

    @property
    def complete(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class CoverageManifest:
    candidate: FinalCandidate
    expected_en_ids: tuple[str, ...]
    payload_en_ids: tuple[str, ...]
    expected_ru_ids: tuple[str, ...]
    payload_ru_ids: tuple[str, ...]
    batches: tuple[tuple[ReviewUnit, ...], ...]
    issues: tuple[ReviewIssue, ...]

    @property
    def complete(self) -> bool:
        return not self.issues


def _inventory(path: str, snapshot: str, content: bytes) -> BlockInventory:
    text = content.decode("utf-8")
    digest = sha256(content).hexdigest()
    located = parse_review_blocks(text)
    blocks = tuple(ReviewBlock(
        sha256(f"{snapshot}\0{path}\0{digest}\0{b.address}".encode()).hexdigest(),
        path, snapshot, digest, b.kind, b.address, b.span,
        b.line_start, b.line_end, text[b.span.start:b.span.end],
        b.structure, b.heading_level, b.anchor,
    ) for b in located)
    cursor = 0
    for block in blocks:
        if not cursor <= block.span.start < block.span.end <= len(text):
            raise ValueError("overlapping or invalid source spans")
        if text[cursor:block.span.start].strip():
            raise ValueError("unparsed readable source between blocks")
        cursor = block.span.end
    if text[cursor:].strip():
        raise ValueError("unparsed readable source tail")
    return BlockInventory(path, snapshot, digest, text, blocks,
                          "whitespace-only" if not text.strip() else None)


def prepare_review_document(
    candidate: FinalCandidate, source: AuthoritativeDocument,
    en_path: str, en_bytes: bytes | None,
) -> ReviewPlan:
    """Prepare one explicitly authoritative RU/EN file pair from frozen bytes.

    Production callers should use load_review_document to obtain EN from K.
    Failed extraction retains the entire raw document as located evidence.
    """
    issues = []

    def inventory(path: str, snapshot: str, content: bytes | None) -> BlockInventory:
        raw = content or b""
        try:
            if content is None:
                raise ValueError("missing finalized target")
            return _inventory(path, snapshot, raw)
        except (ValueError, TypeError, IndexError) as exc:
            text = raw.decode("utf-8", errors="replace")
            issues.append(ReviewIssue(candidate.commit_sha, path, SourceSpan(0, len(text)), str(exc)))
            digest = sha256(raw).hexdigest()
            block = ReviewBlock(f"unparsed:{snapshot}:{path}:{digest}", path, snapshot,
                                digest, "unparsed", (), SourceSpan(0, len(text)),
                                1, max(1, len(text.splitlines())), text)
            return BlockInventory(path, snapshot, digest, text, (block,) if text else ())

    ru = replace(inventory(source.path, source.source_id, source.content),
                 candidate_sha=candidate.commit_sha, candidate_tree_sha=candidate.tree_sha)
    en = replace(inventory(en_path, candidate.commit_sha, en_bytes),
                 candidate_sha=candidate.commit_sha, candidate_tree_sha=candidate.tree_sha)
    if en_path not in candidate.en_paths or not source.source_id:
        issues.append(ReviewIssue(candidate.commit_sha, en_path, SourceSpan(0, len(en.text)),
                                  "unproved candidate membership or source authority"))
    ru_ids = tuple(b.id for b in ru.blocks)
    en_ids = tuple(b.id for b in en.blocks)
    if tuple(b.kind for b in ru.blocks) != tuple(b.kind for b in en.blocks):
        issues.append(ReviewIssue(candidate.commit_sha, en_path, SourceSpan(0, len(en.text)),
                                  "missing or structurally unmatched whole blocks", en_ids, ru_ids))
    units = ()
    if not issues and (ru_ids or en_ids):
        units, alignment_issues = _align_blocks(candidate, ru, en)
        issues.extend(alignment_issues)
    return ReviewPlan(candidate, ru, en, units, tuple(issues))


def _heading_paths(blocks: tuple[ReviewBlock, ...]) -> tuple[tuple[int, ...], ...]:
    stack: list[int] = []
    paths = []
    for index, block in enumerate(blocks):
        if block.heading_level:
            while stack and blocks[stack[-1]].heading_level >= block.heading_level:
                stack.pop()
        paths.append(tuple(stack))
        if block.heading_level:
            stack.append(index)
    return tuple(paths)


def _section_shapes(blocks: tuple[ReviewBlock, ...]) -> dict[int, tuple[tuple[str, ...], ...]]:
    shapes = {}
    for index, block in enumerate(blocks):
        if not block.heading_level:
            continue
        end = index + 1
        while end < len(blocks):
            level = blocks[end].heading_level
            if level and level <= block.heading_level:
                break
            end += 1
        shapes[index] = tuple(b.structure for b in blocks[index:end])
    return shapes


def _align_blocks(candidate: FinalCandidate, ru: BlockInventory,
                  en: BlockInventory) -> tuple[tuple[ReviewUnit, ...], tuple[ReviewIssue, ...]]:
    if tuple(b.kind for b in ru.blocks) != tuple(b.kind for b in en.blocks):
        return (), (ReviewIssue(candidate.commit_sha, en.path, SourceSpan(0, len(en.text)),
                    "missing or structurally unmatched whole blocks",
                    tuple(b.id for b in en.blocks), tuple(b.id for b in ru.blocks)),)
    paths = _heading_paths(ru.blocks)
    target_paths = _heading_paths(en.blocks)
    sibling_counts = Counter((paths[i], b.heading_level) for i, b in enumerate(ru.blocks) if b.heading_level)
    source_shapes, target_shapes = _section_shapes(ru.blocks), _section_shapes(en.blocks)
    sibling_shapes = Counter((paths[i], shape) for i, shape in source_shapes.items())
    target_sibling_shapes = Counter((target_paths[i], shape) for i, shape in target_shapes.items())
    source_anchors = Counter(b.anchor for b in ru.blocks if b.anchor)
    target_anchors = Counter(b.anchor for b in en.blocks if b.anchor)
    headings: dict[int, bool] = {}
    for i, (rb, eb) in enumerate(zip(ru.blocks, en.blocks, strict=True)):
        if rb.heading_level:
            anchor_proof = (rb.anchor == eb.anchor and rb.anchor is not None
                            and source_anchors[rb.anchor] == target_anchors[eb.anchor] == 1)
            distinct_section = (source_shapes[i] == target_shapes.get(i)
                                and sibling_shapes[(paths[i], source_shapes[i])] == 1
                                and target_sibling_shapes[(target_paths[i], source_shapes[i])] == 1)
            unique_child = (rb.anchor is None and eb.anchor is None
                            and (sibling_counts[(paths[i], rb.heading_level)] == 1 or distinct_section))
            headings[i] = (paths[i] == target_paths[i] and rb.structure == eb.structure
                           and all(headings.get(parent, False) for parent in paths[i])
                           and (anchor_proof or unique_child or ru.text == en.text))

    units = []
    issues = []
    i = 0
    while i < len(ru.blocks):
        rb, eb = ru.blocks[i], en.blocks[i]
        parent_proven = bool(paths[i]) and all(headings.get(parent, False) for parent in paths[i])
        # A single unique root container also bounds the prose on either side.
        # This excludes a file consisting solely of repeated root paragraphs.
        root_boundaries = [b.structure for b in ru.blocks if not b.heading_level and b.kind != "paragraph"]
        root_proven = (not paths[i] and not any(b.heading_level for b in ru.blocks)
                       and bool(root_boundaries) and len(set(root_boundaries)) == len(root_boundaries)
                       and tuple(b.structure for b in ru.blocks) == tuple(b.structure for b in en.blocks))
        proven = (rb.structure == eb.structure and paths[i] == target_paths[i]
                  and (ru.text == en.text or len(ru.blocks) == 1
                       or headings.get(i, False) or parent_proven or root_proven
                       or (rb.kind == "front_matter" and i == 0)))
        if not proven:
            issues.append(ReviewIssue(candidate.commit_sha, en.path, eb.span,
                          "ambiguous whole-block correspondence", (eb.id,), (rb.id,)))
            i += 1
            continue
        end = i + 1
        # Group only a local indistinguishable run, bounded by a proven heading
        # or structural container. Never collapse the whole document to align it.
        if ru.text != en.text and not rb.heading_level and (parent_proven or root_proven):
            while end < len(ru.blocks) and paths[end] == paths[i] and target_paths[end] == paths[i]:
                if ru.blocks[end].structure != rb.structure or en.blocks[end].structure != rb.structure:
                    break
                end += 1
        rbs, ebs = ru.blocks[i:end], en.blocks[i:end]
        ru_ids, en_ids = tuple(b.id for b in rbs), tuple(b.id for b in ebs)
        identity = sha256("\0".join((*ru_ids, *en_ids)).encode()).hexdigest()
        context = tuple(text for parent in paths[i]
                        for text in (ru.blocks[parent].text, en.blocks[parent].text))
        units.append(ReviewUnit(identity, candidate.commit_sha, candidate.tree_sha,
            ru.path, en.path, ru.snapshot_id, ru_ids, en_ids,
            ru.text[rbs[0].span.start:rbs[-1].span.end],
            en.text[ebs[0].span.start:ebs[-1].span.end],
            "enclosing-structure" if parent_proven or root_proven else "authoritative-file-pair", context))
        i = end
    return tuple(units), tuple(issues)


def load_review_document(repo_path: str, candidate: FinalCandidate,
                         source: AuthoritativeDocument, en_path: str) -> ReviewPlan:
    return prepare_review_document(candidate, source, en_path,
                                   read_candidate_bytes(repo_path, candidate, en_path))


def serialize_review_batch(units: tuple[ReviewUnit, ...]) -> str:
    return json.dumps({"units": [asdict(unit) for unit in units]}, ensure_ascii=False,
                      separators=(",", ":"))


def _inventory_matches_candidate(inventory: BlockInventory, candidate: FinalCandidate) -> bool:
    if (inventory.candidate_sha, inventory.candidate_tree_sha) != (candidate.commit_sha, candidate.tree_sha):
        return False
    try:
        canonical = _inventory(inventory.path, inventory.snapshot_id, inventory.text.encode("utf-8"))
    except (ValueError, TypeError, IndexError):
        return False
    return canonical == replace(inventory, candidate_sha="", candidate_tree_sha="")


def validate_payloads(plan: ReviewPlan, batches: tuple[tuple[ReviewUnit, ...], ...]) -> CoverageManifest:
    """Validate actual typed payloads, including exact text, before transmission."""
    units = tuple(unit for batch in batches for unit in batch)
    en_ids = tuple(b.id for b in plan.en.blocks)
    ru_ids = tuple(b.id for b in plan.ru.blocks)
    sent_en = tuple(i for unit in units for i in unit.en_block_ids)
    sent_ru = tuple(i for unit in units for i in unit.ru_block_ids)
    issues = list(plan.issues)
    identity_valid = (plan.en.snapshot_id == plan.candidate.commit_sha
                      and plan.en.path in plan.candidate.en_paths
                      and bool(plan.ru.snapshot_id)
                      and _inventory_matches_candidate(plan.ru, plan.candidate)
                      and _inventory_matches_candidate(plan.en, plan.candidate))
    if identity_valid and not plan.issues:
        canonical_units, alignment_issues = _align_blocks(plan.candidate, plan.ru, plan.en)
        identity_valid = not alignment_issues and canonical_units == plan.units
    if not identity_valid or any(
        (unit.candidate_sha, unit.candidate_tree_sha) !=
        (plan.candidate.commit_sha, plan.candidate.tree_sha)
        for unit in (*plan.units, *units)
    ):
        issues.append(ReviewIssue(plan.candidate.commit_sha, plan.en.path,
            SourceSpan(0, len(plan.en.text)), "candidate or inventory identity mismatch", en_ids, ru_ids))
    expected = {unit.id: unit for unit in plan.units}
    if (Counter(sent_en) != Counter(en_ids) or Counter(sent_ru) != Counter(ru_ids)
            or Counter(unit.id for unit in units) != Counter(unit.id for unit in plan.units)
            or any(expected.get(unit.id) != unit for unit in units)):
        bad_en = tuple(i for i in set(en_ids) | set(sent_en)
                       if Counter(en_ids)[i] != Counter(sent_en)[i])
        bad_ru = tuple(i for i in set(ru_ids) | set(sent_ru)
                       if Counter(ru_ids)[i] != Counter(sent_ru)[i])
        issues.append(ReviewIssue(plan.candidate.commit_sha, plan.en.path,
                                  SourceSpan(0, len(plan.en.text)),
                                  "missing, duplicate, altered, or stale reviewer payload",
                                  bad_en or en_ids, bad_ru or ru_ids))
    return CoverageManifest(plan.candidate, en_ids, sent_en, ru_ids, sent_ru, batches, tuple(issues))


def batch_review_units(plan: ReviewPlan, *, budget_bytes: int,
                       hard_limit_bytes: int, overhead_bytes: int = 0) -> CoverageManifest:
    """Budget actual UTF-8 JSON plus caller-supplied prompt/transport overhead."""
    if budget_bytes <= 0 or hard_limit_bytes <= 0 or overhead_bytes < 0:
        raise ValueError("invalid reviewer request budget")
    batches: list[tuple[ReviewUnit, ...]] = []
    pending: tuple[ReviewUnit, ...] = ()
    overflow = []
    for unit in plan.units:
        size = len(serialize_review_batch((unit,)).encode()) + overhead_bytes
        if size > hard_limit_bytes:
            blocks = [b for b in plan.en.blocks if b.id in unit.en_block_ids]
            span = SourceSpan(min(b.span.start for b in blocks), max(b.span.end for b in blocks)) if blocks else SourceSpan(0, len(plan.en.text))
            overflow.append(ReviewIssue(plan.candidate.commit_sha, unit.en_path,
                span, "whole unit exceeds reviewer hard limit",
                unit.en_block_ids, unit.ru_block_ids))
            continue
        combined = len(serialize_review_batch((*pending, unit)).encode()) + overhead_bytes
        if pending and combined > min(budget_bytes, hard_limit_bytes):
            batches.append(pending)
            pending = ()
        pending = (*pending, unit)
    if pending:
        batches.append(pending)
    manifest = validate_payloads(plan, tuple(batches))
    if overflow:
        manifest = replace(manifest, issues=tuple(overflow) + manifest.issues)
    return manifest
