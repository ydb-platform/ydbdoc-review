"""Immutable, lossless preparation evidence for final-candidate semantic review.

Document pairing is explicit authority supplied by the caller. Unanchored
translated blocks are reviewed together in that safely paired container; equal
counts/kinds are only a rejection guard, never proof of positional alignment.
This manifest proves input delivery, not model completion or verdict quality.
"""

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
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

    ru = inventory(source.path, source.source_id, source.content)
    en = inventory(en_path, candidate.commit_sha, en_bytes)
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
        pairs = tuple(zip(ru.blocks, en.blocks, strict=True))
        anchors = [re.search(r"\{#([^}]+)\}", b.text) if b.kind == "heading" else None
                   for b in ru.blocks]
        anchor_ids = [m.group(1) for m in anchors if m]
        proven = []
        for (rb, eb), anchor in zip(pairs, anchors, strict=True):
            target_anchor = re.search(r"\{#([^}]+)\}", eb.text) if eb.kind == "heading" else None
            anchored = (anchor is not None and target_anchor is not None
                        and anchor.group(1) == target_anchor.group(1)
                        and anchor_ids.count(anchor.group(1)) == 1)
            if len(pairs) != 1 and ru.text != en.text and not anchored:
                issues.append(ReviewIssue(candidate.commit_sha, en_path, eb.span,
                    "ambiguous whole-block correspondence", (eb.id,), (rb.id,)))
                continue
            identity = sha256(f"{rb.id}\0{eb.id}".encode()).hexdigest()
            proven.append(ReviewUnit(identity, candidate.commit_sha, candidate.tree_sha,
                source.path, en_path, source.source_id, (rb.id,), (eb.id,), rb.text, eb.text,
                "stable-heading-anchor" if anchored else
                "identical-document" if ru.text == en.text else "authoritative-file-pair",
                (ru.text, en.text) if len(pairs) > 1 else ()))
        units = tuple(proven)
    return ReviewPlan(candidate, ru, en, units, tuple(issues))


def load_review_document(repo_path: str, candidate: FinalCandidate,
                         source: AuthoritativeDocument, en_path: str) -> ReviewPlan:
    return prepare_review_document(candidate, source, en_path,
                                   read_candidate_bytes(repo_path, candidate, en_path))


def serialize_review_batch(units: tuple[ReviewUnit, ...]) -> str:
    return json.dumps({"units": [asdict(unit) for unit in units]}, ensure_ascii=False,
                      separators=(",", ":"))


def validate_payloads(plan: ReviewPlan, batches: tuple[tuple[ReviewUnit, ...], ...]) -> CoverageManifest:
    """Validate actual typed payloads, including exact text, before transmission."""
    units = tuple(unit for batch in batches for unit in batch)
    en_ids = tuple(b.id for b in plan.en.blocks)
    ru_ids = tuple(b.id for b in plan.ru.blocks)
    sent_en = tuple(i for unit in units for i in unit.en_block_ids)
    sent_ru = tuple(i for unit in units for i in unit.ru_block_ids)
    issues = list(plan.issues)
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
            overflow.append(ReviewIssue(plan.candidate.commit_sha, unit.en_path,
                SourceSpan(0, len(unit.en_text)), "whole unit exceeds reviewer hard limit",
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
        from dataclasses import replace
        manifest = replace(manifest, issues=tuple(overflow) + manifest.issues)
    return manifest
