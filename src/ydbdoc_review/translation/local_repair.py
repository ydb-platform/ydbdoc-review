"""Bounded one-block critic and repair controller from one-pass-v010."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.acquisition import (
    AcquisitionController,
    AcquisitionExhaustedError,
    AcquisitionProtocolError,
)
from ydbdoc_review.translation.model_policy import ModelPair, TranslationChatOnce

MAX_FINDINGS = 4
MAX_REPAIR_CALLS = 8
MAX_CRITIC_EVALUATIONS = 9


@dataclass(frozen=True)
class Finding:
    finding_id: str
    rule_id: str
    severity: str
    block_id: str
    start: int
    end: int
    atom_ids: tuple[str, ...]
    repair_class: str
    message: str
    required_rule: str
    context: str


@dataclass(frozen=True)
class RepairAttempt:
    ordinal: int
    before_hash: str
    candidate_hash: str | None
    outcome: str


@dataclass(frozen=True)
class LocalRepairResult:
    text: str
    critic_evaluations: int
    repair_calls: int
    reports: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _ValidatedRepairCandidate:
    replacement: str
    proposed_document: str
    candidate_hash: str


_REPAIRABLE_RULES = {
    "grammar": "prose",
    "terminology": "prose",
    "mistranslation": "prose",
    "omission": "prose",
    "residual_cyrillic_prose": "prose",
    "cyrillic_anchor": "english_anchor",
}

_NON_REPAIRABLE_RULES = {
    "link_parity",
    "href_parity",
    "fragment_parity",
    "ascii_anchor_parity",
    "protected_atom",
    "markdown_structure",
    "yfm_structure",
    "code_or_config",
}

_TOKEN = re.compile(r"⟦[A-Z][A-Za-z0-9_-]*⟧")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _canonical(
    raw: dict, blocks: dict[str, str], atom_ids_by_block: dict[str, frozenset[str]]
) -> Finding:
    block_id = str(raw["block_id"])
    rule = str(raw["rule_id"])
    if block_id not in blocks or rule not in (set(_REPAIRABLE_RULES) | _NON_REPAIRABLE_RULES):
        raise AcquisitionProtocolError("unknown rule or unstable block")
    severity = str(raw["severity"])
    if severity not in {"RED", "YELLOW", "INFO"}:
        raise AcquisitionProtocolError("invalid severity")
    range_data = raw["range"]
    start, end = int(range_data["start"]), int(range_data["end"])
    if start < 0 or end < start or end > len(blocks[block_id].encode()):
        raise AcquisitionProtocolError("invalid UTF-8 range")
    atoms = tuple(sorted(str(value) for value in raw.get("atom_ids", [])))
    if len(atoms) != len(set(atoms)) or not set(atoms).issubset(
        atom_ids_by_block.get(block_id, frozenset())
    ):
        raise AcquisitionProtocolError("unknown or duplicated protected atom id")
    fingerprint = " ".join(str(raw.get("message", "")).lower().split())
    canonical = _hash(f"{rule}|{block_id}|{start}:{end}|{atoms}|{fingerprint}")
    declared_class = str(raw["repair_class"])
    registered_class = _REPAIRABLE_RULES.get(rule, "not_repairable")
    if declared_class != registered_class:
        raise AcquisitionProtocolError("repair class does not match registered rule")
    return Finding(
        canonical,
        rule,
        severity,
        block_id,
        start,
        end,
        atoms,
        registered_class,
        str(raw.get("message", "")),
        str(raw["required_rule"]),
        str(raw["context"]),
    )


def _byte_to_char(text: str, offset: int) -> int:
    raw = text.encode("utf-8")
    try:
        return len(raw[:offset].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise AcquisitionProtocolError("range is not on a UTF-8 boundary") from exc


def _allowed_repair_range(text: str, start_byte: int, end_byte: int) -> tuple[int, int]:
    start = _byte_to_char(text, start_byte)
    end = _byte_to_char(text, end_byte)
    boundaries = [0]
    boundaries.extend(match.end() for match in re.finditer(r"[.!?](?:\s+|$)", text))
    if boundaries[-1] != len(text):
        boundaries.append(len(text))
    sentence = next(
        (index for index in range(len(boundaries) - 1) if boundaries[index] <= start < boundaries[index + 1]),
        max(0, len(boundaries) - 2),
    )
    end_sentence = next(
        (index for index in range(sentence, len(boundaries) - 1) if end <= boundaries[index + 1]),
        len(boundaries) - 2,
    )
    return boundaries[max(0, sentence - 1)], boundaries[min(len(boundaries) - 1, end_sentence + 2)]


def _replacement_within_range(before: str, replacement: str, finding: Finding) -> bool:
    allowed_start, allowed_end = _allowed_repair_range(before, finding.start, finding.end)
    for tag, i1, i2, _j1, _j2 in SequenceMatcher(None, before, replacement).get_opcodes():
        if tag == "equal":
            continue
        # Insertions have i1 == i2; their insertion point must be in range.
        if i2 < allowed_start or i1 > allowed_end:
            return False
    return True


def _failure_report(
    finding: Finding,
    source_file: str,
    attempts: list[RepairAttempt],
    terminal_reason: str,
) -> dict[str, object]:
    return {
        "category": "local_repair_failed",
        "source_file": source_file,
        "output_file": source_file.replace("/ru/", "/en/", 1),
        "finding_id": finding.finding_id,
        "rule_id": finding.rule_id,
        "severity": "RED",
        "block_id": finding.block_id,
        "range": {"start": finding.start, "end": finding.end},
        "atom_ids": list(finding.atom_ids),
        "attempts": [attempt.__dict__ for attempt in attempts],
        "terminal_reason": terminal_reason,
        "manual_action": finding.message,
    }


def acquire_english_anchor(
    *,
    client: TranslationChatOnce,
    repair_models: ModelPair,
    source_anchor: str,
    english_heading: str,
    used_anchors: set[str],
) -> tuple[str, int]:
    """Acquire only one constrained ASCII anchor, at most two logical calls."""
    finding_id = _hash(f"cyrillic_anchor|{source_anchor}|{english_heading}")
    seen: set[str] = set()
    for logical_attempt in (1, 2):
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "finding_id": finding_id,
                        "block_id": "heading-anchor",
                        "source_anchor": source_anchor,
                        "english_heading": english_heading,
                        "required_rule": "return one unique ASCII Markdown anchor",
                    },
                    ensure_ascii=False,
                ),
            }
        ]

        def validate(response: object) -> str:
            raw = json.loads(response.content)
            if raw.get("finding_id") != finding_id or raw.get("block_id") != "heading-anchor":
                raise AcquisitionProtocolError("finding/block mismatch")
            replacement = raw.get("replacement")
            if not isinstance(replacement, str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]+", replacement
            ):
                raise AcquisitionProtocolError("invalid English anchor token")
            if replacement in used_anchors:
                raise AcquisitionProtocolError("English anchor collision")
            return replacement

        try:
            candidate = AcquisitionController(
                client,
                repair_models,
                role="repair",
                parser=validate,
            ).acquire(messages).payload
        except AcquisitionExhaustedError:
            continue
        if candidate in seen:
            raise AcquisitionProtocolError("English anchor oscillation")
        seen.add(candidate)
        return candidate, logical_attempt
    raise AcquisitionProtocolError("English anchor repair attempts exhausted")


def run_bounded_local_repair(
    text: str,
    ru_text: str,
    client: TranslationChatOnce,
    *,
    critic_models: ModelPair,
    repair_models: ModelPair,
    validation_context,
    validate_complete_document=None,
    source_file: str = "",
    repair_calls_used: int = 0,
    repair_findings_used: int = 0,
) -> LocalRepairResult:
    if validate_complete_document is None:
        from ydbdoc_review.translation.one_pass import (
            validate_complete_document as document_validator,
        )
    else:
        document_validator = validate_complete_document

    ru_segments = extract_segments(parse_markdown(ru_text))
    ru_blocks = {segment.id: segment.text for segment in ru_segments}
    atom_manifest = {
        segment.id: tuple(
            {
                "id": placeholder.placeholder,
                "sha256": _hash(placeholder.node.model_dump_json()),
            }
            for placeholder in segment.placeholders
        )
        for segment in ru_segments
    }
    atom_ids_by_block = {
        block_id: frozenset(item["id"] for item in atoms)
        for block_id, atoms in atom_manifest.items()
    }
    current = text
    evaluations = 0
    repairs = repair_calls_used
    attempts_by_finding: dict[str, list[RepairAttempt]] = {}
    budget_owner_by_block: dict[str, str] = {}
    owner_findings: dict[str, Finding] = {}
    seen_hashes: dict[str, set[str]] = {}
    reports: list[dict[str, object]] = []
    cached_findings: list[Finding] | None = None

    while True:
        blocks = {segment.id: segment.text for segment in extract_segments(parse_markdown(current))}
        if set(blocks) != set(ru_blocks):
            raise AcquisitionProtocolError("translated block identity drift")
        if cached_findings is None:
            if evaluations >= MAX_CRITIC_EVALUATIONS:
                reports.append({"category": "local_repair_failed", "terminal_reason": "document_cap", "attempts": []})
                break
            block_records = {
                block_id: {
                    "block_id": block_id,
                    "en_editable_prose": en_prose,
                    "corresponding_ru_prose": ru_blocks[block_id],
                    "allowed_range": [0, len(en_prose.encode("utf-8"))],
                    "atom_manifest": atom_manifest[block_id],
                }
                for block_id, en_prose in blocks.items()
            }
            critic_messages = [{"role": "user", "content": json.dumps(
                {"block_records": block_records}, ensure_ascii=False
            )}]

            def validate_critic(
                response: object, current_blocks: dict[str, str] = blocks
            ) -> list[Finding]:
                try:
                    raw = json.loads(response.content)
                    return [
                        _canonical(item, current_blocks, atom_ids_by_block)
                        for item in raw["findings"]
                    ]
                except Exception as exc:
                    raise AcquisitionProtocolError(str(exc)) from exc

            findings = AcquisitionController(
                client,
                critic_models,
                role="critic",
                parser=validate_critic,
            ).acquire(critic_messages).payload
            evaluations += 1
        else:
            findings = cached_findings
            cached_findings = None
        red = sorted((f for f in findings if f.severity == "RED"), key=lambda f: (list(blocks).index(f.block_id), f.start, f.rule_id, f.finding_id))
        if not red:
            document_validator(current, validation_context)
            return LocalRepairResult(current, evaluations, repairs, tuple(reports))
        finding = red[0]
        owner_id = budget_owner_by_block.setdefault(finding.block_id, finding.finding_id)
        owner_findings.setdefault(owner_id, finding)
        owner_finding = owner_findings[owner_id]
        issued = attempts_by_finding.setdefault(owner_id, [])
        if finding.repair_class not in {"prose", "english_anchor"}:
            reports.append(_failure_report(owner_finding, source_file, issued, "conflict"))
            break
        if repair_findings_used + len(attempts_by_finding) > MAX_FINDINGS or repairs >= MAX_REPAIR_CALLS or len(issued) >= 2:
            reports.append(_failure_report(owner_finding, source_file, issued, "attempts_exhausted" if len(issued) >= 2 else "document_cap"))
            break
        before = blocks[finding.block_id]
        repair_messages = [{"role": "user", "content": json.dumps(
            {
                "finding_id": finding.finding_id,
                "block_id": finding.block_id,
                "range": [finding.start, finding.end],
                "ru_prose": ru_blocks[finding.block_id],
                "editable_block": before,
                "atom_manifest": atom_manifest[finding.block_id],
                "required_rule": finding.required_rule,
                "context": finding.context,
            },
            ensure_ascii=False,
        )}]

        def validate_repair(
            response: object,
            current_finding: Finding = finding,
            current_before: str = before,
            current_document: str = current,
            current_validation_context=validation_context,
        ) -> _ValidatedRepairCandidate:
            try:
                raw = json.loads(response.content)
                if not isinstance(raw, dict):
                    raise AcquisitionProtocolError("repair response must be an object")
                if raw.get("finding_id") != current_finding.finding_id:
                    raise AcquisitionProtocolError("finding mismatch")
                if raw.get("block_id") != current_finding.block_id:
                    raise AcquisitionProtocolError("block mismatch")
                replacement = raw.get("replacement")
                if not isinstance(replacement, str) or not replacement.strip():
                    raise AcquisitionProtocolError("invalid replacement")
                if tuple(_TOKEN.findall(current_before)) != tuple(
                    _TOKEN.findall(replacement)
                ):
                    raise AcquisitionProtocolError("protected token sequence mismatch")
                if not _replacement_within_range(
                    current_before, replacement, current_finding
                ):
                    raise AcquisitionProtocolError("replacement outside allowed range")
                if current_document.count(current_before) != 1:
                    raise AcquisitionProtocolError("repair target is not unique")
                proposed_document = current_document.replace(
                    current_before, replacement, 1
                )
                document_validator(proposed_document, current_validation_context)
                return _ValidatedRepairCandidate(
                    replacement=replacement,
                    proposed_document=proposed_document,
                    candidate_hash=_hash(replacement),
                )
            except AcquisitionProtocolError:
                raise
            except Exception as exc:
                raise AcquisitionProtocolError(str(exc)) from exc

        repairs += 1
        try:
            candidate = AcquisitionController(
                client,
                repair_models,
                role="repair",
                parser=validate_repair,
            ).acquire(repair_messages).payload
            candidate_hash = candidate.candidate_hash
            history = seen_hashes.setdefault(finding.block_id, {_hash(before)})
            if candidate_hash in history:
                issued.append(RepairAttempt(len(issued) + 1, _hash(before), candidate_hash, "oscillation"))
                reports.append(_failure_report(owner_finding, source_file, issued, "oscillation"))
                break
            history.add(candidate_hash)
            issued.append(RepairAttempt(len(issued) + 1, _hash(before), candidate_hash, "recritic"))
            current = candidate.proposed_document
        except AcquisitionExhaustedError:
            issued.append(RepairAttempt(len(issued) + 1, _hash(before), None, "acquisition"))
            cached_findings = findings
            continue

    return LocalRepairResult(current, evaluations, repairs, tuple(reports))
