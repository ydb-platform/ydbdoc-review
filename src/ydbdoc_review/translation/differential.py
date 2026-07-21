"""Differential (incremental) translation: translate only changed RU segments.

Revises §6.30 for the common case: when EN exists and RU change magnitude is
low, seed unchanged segment translations from existing EN and LLM-translate
only added/modified segments. Fall back to full re-translate on edge cases
(no EN, incomplete EN, high magnitude, stale EN, structure mismatch).

See AGENT_TASK_DIFFERENTIAL_TRANSLATION.md and Memory Bank §6.132.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Literal

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import align_translations_from_target
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.errors import TranslationValidationError

logger = logging.getLogger(__name__)

TranslationMode = Literal["full", "differential", "skip"]
ChangeType = Literal["new_file", "deleted_file", "modified"]
MergeStrategy = Literal["reconstruct", "patch"]

_ENV_ENABLE = "YDBDOC_DIFFERENTIAL_TRANSLATION"


@dataclass(frozen=True)
class DifferentialTranslationConfig:
    """Thresholds for full vs differential (task § Configuration)."""

    enabled: bool = True
    stale_days_threshold: int = 90
    change_magnitude_threshold: float = 0.5
    min_en_file_ratio: float = 0.3
    enable_fuzzy_matching: bool = True
    fuzzy_match_threshold: float = 0.8

    @classmethod
    def from_env_and_defaults(
        cls,
        *,
        enabled: bool | None = None,
        stale_days_threshold: int = 90,
        change_magnitude_threshold: float = 0.5,
        min_en_file_ratio: float = 0.3,
    ) -> DifferentialTranslationConfig:
        if enabled is None:
            raw = (os.environ.get(_ENV_ENABLE) or "1").strip().lower()
            enabled = raw not in {"0", "false", "no", "off"}
        return cls(
            enabled=enabled,
            stale_days_threshold=stale_days_threshold,
            change_magnitude_threshold=change_magnitude_threshold,
            min_en_file_ratio=min_en_file_ratio,
        )


@dataclass(frozen=True)
class TextBlock:
    """Markdown block for planning APIs (backed by pipeline segments)."""

    kind: str
    content: str
    line_range: tuple[int, int]
    heading_level: int | None = None
    segment_id: str | None = None


@dataclass(frozen=True)
class TranslationStrategy:
    mode: TranslationMode
    reason: str
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RuDiffAnalysis:
    change_type: ChangeType
    added_blocks: list[TextBlock]
    modified_blocks: list[TextBlock]
    removed_blocks: list[TextBlock]
    change_magnitude: float
    # Segment-id sets for executor (PR-side ids)
    added_segment_ids: frozenset[str] = frozenset()
    modified_segment_ids: frozenset[str] = frozenset()
    kept_segment_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DifferentialTranslationPlan:
    added_blocks: list[TextBlock]
    modified_blocks: list[TextBlock]
    en_blocks_to_keep: list[TextBlock]
    merge_strategy: MergeStrategy
    """PR segment_id → EN protected text to seed (unchanged)."""
    seeded_translations: dict[str, str] = field(default_factory=dict)
    """PR segment ids that still need LLM translation."""
    pending_segment_ids: frozenset[str] = frozenset()


def parse_markdown_blocks(text: str) -> list[TextBlock]:
    """Parse markdown into TextBlocks via the existing segment extractor."""
    segments = extract_segments(parse_markdown(text))
    return [_segment_to_block(seg) for seg in segments]


def _segment_to_block(seg: Segment) -> TextBlock:
    heading_level = None
    if seg.kind.value == "heading" and seg.path:
        # path often like ["H2: Title"] — level unknown; leave None
        heading_level = None
    return TextBlock(
        kind=seg.kind.value,
        content=seg.text,
        line_range=(0, 0),
        heading_level=heading_level,
        segment_id=seg.id,
    )


def _segment_key(seg: Segment) -> tuple[str, str]:
    return (seg.kind.value, seg.text)


def is_change_magnitude_high(
    ru_diff_analysis: RuDiffAnalysis,
    threshold: float = 0.5,
) -> bool:
    magnitude = ru_diff_analysis.change_magnitude
    if magnitude > threshold:
        logger.info(
            "Change magnitude %.1f%% > threshold %.1f%% — use FULL translation",
            magnitude * 100,
            threshold * 100,
        )
        return True
    return False


def is_en_file_incomplete(
    en_text: str,
    ru_text: str,
    *,
    min_en_file_ratio: float = 0.3,
) -> bool:
    ru_len = max(len(ru_text.strip()), 1)
    ratio = len(en_text.strip()) / ru_len
    return ratio < min_en_file_ratio


def is_en_file_stale(
    *,
    last_modified: datetime | None,
    stale_days: int = 90,
    now: datetime | None = None,
) -> bool:
    if last_modified is None:
        return False
    now = now or datetime.now(timezone.utc)
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=timezone.utc)
    age_days = (now - last_modified).days
    if age_days > stale_days:
        logger.info(
            "EN file is stale (last modified %d days ago, threshold %d)",
            age_days,
            stale_days,
        )
        return True
    return False


def get_last_commit_date(repo_path: str, path: str) -> datetime | None:
    """Best-effort last commit date for ``path`` in ``repo_path``."""
    try:
        out = subprocess.check_output(
            ["git", "-C", repo_path, "log", "-1", "--format=%cI", "--", path],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).strip()
    except (subprocess.SubprocessError, OSError, TimeoutError):
        return None
    if not out:
        return None
    try:
        return datetime.fromisoformat(out)
    except ValueError:
        return None


def _align_pr_to_base(
    base_segments: list[Segment],
    pr_segments: list[Segment],
) -> list[tuple[Segment, Segment | None]]:
    """For each PR segment, the equal base segment or None if added/replaced."""
    base_keys = [_segment_key(s) for s in base_segments]
    pr_keys = [_segment_key(s) for s in pr_segments]
    matcher = SequenceMatcher(a=base_keys, b=pr_keys, autojunk=False)
    pairs: list[tuple[Segment, Segment | None]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for bi, pi in zip(range(i1, i2), range(j1, j2), strict=True):
                pairs.append((pr_segments[pi], base_segments[bi]))
        elif tag in {"replace", "insert"}:
            for pi in range(j1, j2):
                pairs.append((pr_segments[pi], None))
        # delete: base-only, no PR segment
    return pairs


def analyze_ru_diff(
    ru_base_text: str | None,
    ru_pr_text: str,
) -> RuDiffAnalysis:
    """Compare base RU vs PR RU at segment granularity."""
    if ru_base_text is None or not ru_base_text.strip():
        pr_blocks = parse_markdown_blocks(ru_pr_text)
        ids = frozenset(b.segment_id for b in pr_blocks if b.segment_id)
        return RuDiffAnalysis(
            change_type="new_file",
            added_blocks=pr_blocks,
            modified_blocks=[],
            removed_blocks=[],
            change_magnitude=1.0,
            added_segment_ids=ids,
            modified_segment_ids=frozenset(),
            kept_segment_ids=frozenset(),
        )

    base_segments = extract_segments(parse_markdown(ru_base_text))
    pr_segments = extract_segments(parse_markdown(ru_pr_text))
    pairs = _align_pr_to_base(base_segments, pr_segments)

    added: list[TextBlock] = []
    modified: list[TextBlock] = []
    kept_ids: set[str] = set()
    added_ids: set[str] = set()
    modified_ids: set[str] = set()

    # Classify: equal → kept; insert/replace without base → added.
    # "Modified" = replace opcode chunks where both sides have content at
    # roughly same structure — SequenceMatcher marks whole replace spans as
    # insert-like (base=None). Treat all base=None as added/modified together.
    base_keys = {_segment_key(s) for s in base_segments}
    for pr_seg, base_seg in pairs:
        block = _segment_to_block(pr_seg)
        if base_seg is not None:
            kept_ids.add(pr_seg.id)
            continue
        # Heuristic: if a similar-length equal-kind segment existed, call modified
        if any(
            s.kind == pr_seg.kind and s.text != pr_seg.text for s in base_segments
        ) and _segment_key(pr_seg) not in base_keys:
            modified.append(block)
            modified_ids.add(pr_seg.id)
        else:
            added.append(block)
            added_ids.add(pr_seg.id)

    # Removed: base segments with no equal PR counterpart
    pr_keys = {_segment_key(s) for s in pr_segments}
    removed = [
        _segment_to_block(s) for s in base_segments if _segment_key(s) not in pr_keys
    ]

    changed = len(added_ids) + len(modified_ids)
    denom = max(len(pr_segments), 1)
    magnitude = min(1.0, changed / denom)

    return RuDiffAnalysis(
        change_type="modified",
        added_blocks=added,
        modified_blocks=modified,
        removed_blocks=removed,
        change_magnitude=magnitude,
        added_segment_ids=frozenset(added_ids),
        modified_segment_ids=frozenset(modified_ids),
        kept_segment_ids=frozenset(kept_ids),
    )


def find_corresponding_en_block(
    ru_block: TextBlock,
    ru_base_blocks: list[TextBlock],
    en_blocks: list[TextBlock],
    *,
    fuzzy_match_threshold: float = 0.8,
) -> TextBlock | None:
    """Map a kept RU block to EN by base position, then fuzzy content."""
    # Positional: same index in base ↔ en when lengths match
    for i, base in enumerate(ru_base_blocks):
        if base.segment_id == ru_block.segment_id or (
            base.kind == ru_block.kind and base.content == ru_block.content
        ):
            if len(en_blocks) == len(ru_base_blocks):
                return en_blocks[i]
            break

    # Fuzzy on content prefix against EN blocks of same kind
    best: TextBlock | None = None
    best_score = 0.0
    needle = ru_block.content[:120]
    for en in en_blocks:
        if en.kind != ru_block.kind:
            continue
        score = SequenceMatcher(None, needle, en.content[:120]).ratio()
        if score > best_score:
            best_score = score
            best = en
    if best is not None and best_score >= fuzzy_match_threshold:
        return best
    return None


class DifferentialTranslationAnalyzer:
    """Decide full vs differential and build a seed plan."""

    def __init__(self, config: DifferentialTranslationConfig | None = None) -> None:
        self.config = config or DifferentialTranslationConfig.from_env_and_defaults()

    def analyze_file_state(
        self,
        *,
        ru_pr_text: str,
        en_current_text: str | None,
        ru_base_text: str | None,
        en_last_modified: datetime | None = None,
    ) -> TranslationStrategy:
        cfg = self.config
        if not cfg.enabled:
            return TranslationStrategy(
                mode="full",
                reason="Differential translation disabled",
                config={"enabled": False},
            )

        if not ru_pr_text.strip():
            return TranslationStrategy(
                mode="skip",
                reason="Empty RU PR text",
                config={},
            )

        if en_current_text is None or not en_current_text.strip():
            return TranslationStrategy(
                mode="full",
                reason="New file in RU PR, creating EN from scratch"
                if not (ru_base_text and ru_base_text.strip())
                else "EN file does not exist; will create from RU PR",
                config={"is_new_file": True},
            )

        if is_en_file_incomplete(
            en_current_text,
            ru_pr_text,
            min_en_file_ratio=cfg.min_en_file_ratio,
        ):
            ratio = len(en_current_text.strip()) / max(len(ru_pr_text.strip()), 1)
            return TranslationStrategy(
                mode="full",
                reason=(
                    f"EN file too small (~{ratio:.0%} of RU), likely incomplete; "
                    "full translation"
                ),
                config={"en_file_incomplete": True, "en_to_ru_ratio": ratio},
            )

        if is_en_file_stale(
            last_modified=en_last_modified,
            stale_days=cfg.stale_days_threshold,
        ):
            return TranslationStrategy(
                mode="full",
                reason=(
                    f"EN file is stale (>{cfg.stale_days_threshold} days), "
                    "will do full retranslation"
                ),
                config={"en_stale": True},
            )

        if ru_base_text is None or not ru_base_text.strip():
            return TranslationStrategy(
                mode="full",
                reason="No RU base text for diff; full translation",
                config={"missing_ru_base": True},
            )

        analysis = analyze_ru_diff(ru_base_text, ru_pr_text)
        if is_change_magnitude_high(analysis, cfg.change_magnitude_threshold):
            return TranslationStrategy(
                mode="full",
                reason=(
                    f"High change magnitude ({analysis.change_magnitude:.0%}); "
                    "full translation safer"
                ),
                config={
                    "change_magnitude": analysis.change_magnitude,
                    "threshold": cfg.change_magnitude_threshold,
                },
            )

        return TranslationStrategy(
            mode="differential",
            reason=(
                f"Low change magnitude ({analysis.change_magnitude:.0%}); "
                "translate only changed segments"
            ),
            config={"change_magnitude": analysis.change_magnitude},
        )

    def plan_translation(
        self,
        *,
        ru_pr_text: str,
        en_current_text: str,
        ru_base_text: str,
        pr_segments: list[Segment] | None = None,
    ) -> DifferentialTranslationPlan:
        """Build seed map: unchanged PR segments ← EN texts aligned to base RU."""
        base_segments = extract_segments(parse_markdown(ru_base_text))
        if pr_segments is None:
            pr_segments = extract_segments(parse_markdown(ru_pr_text))

        try:
            base_en = align_translations_from_target(base_segments, en_current_text)
        except TranslationValidationError as exc:
            logger.info(
                "Cannot align existing EN to base RU (%s) — empty differential plan",
                exc,
            )
            pending = frozenset(s.id for s in pr_segments)
            return DifferentialTranslationPlan(
                added_blocks=[_segment_to_block(s) for s in pr_segments],
                modified_blocks=[],
                en_blocks_to_keep=[],
                merge_strategy="reconstruct",
                seeded_translations={},
                pending_segment_ids=pending,
            )

        pairs = _align_pr_to_base(base_segments, pr_segments)
        seeded: dict[str, str] = {}
        pending: set[str] = set()
        kept_en_blocks: list[TextBlock] = []
        added_blocks: list[TextBlock] = []
        modified_blocks: list[TextBlock] = []

        for pr_seg, base_seg in pairs:
            if base_seg is not None:
                en_text = base_en.get(base_seg.id)
                if en_text is not None:
                    seeded[pr_seg.id] = en_text
                    kept_en_blocks.append(
                        TextBlock(
                            kind=pr_seg.kind.value,
                            content=en_text,
                            line_range=(0, 0),
                            segment_id=pr_seg.id,
                        )
                    )
                    continue
            pending.add(pr_seg.id)
            added_blocks.append(_segment_to_block(pr_seg))

        analysis = analyze_ru_diff(ru_base_text, ru_pr_text)
        # Prefer analysis classification for modified vs added labels
        modified_blocks = [
            b for b in added_blocks if b.segment_id in analysis.modified_segment_ids
        ]
        added_blocks = [
            b for b in added_blocks if b.segment_id not in analysis.modified_segment_ids
        ]

        return DifferentialTranslationPlan(
            added_blocks=added_blocks,
            modified_blocks=modified_blocks,
            en_blocks_to_keep=kept_en_blocks,
            merge_strategy="reconstruct",
            seeded_translations=seeded,
            pending_segment_ids=frozenset(pending),
        )


def prepare_differential_seed(
    *,
    pr_segments: list[Segment],
    ru_pr_text: str,
    en_current_text: str | None,
    ru_base_text: str | None,
    en_last_modified: datetime | None = None,
    config: DifferentialTranslationConfig | None = None,
) -> tuple[TranslationStrategy, dict[str, str], list[Segment]]:
    """Return (strategy, seeded_translations, segments_still_needing_llm).

    On ``full`` / ``skip``, seeded is empty and pending is all ``pr_segments``
    (or empty for skip).
    """
    analyzer = DifferentialTranslationAnalyzer(config)
    strategy = analyzer.analyze_file_state(
        ru_pr_text=ru_pr_text,
        en_current_text=en_current_text,
        ru_base_text=ru_base_text,
        en_last_modified=en_last_modified,
    )
    if strategy.mode != "differential" or not en_current_text or not ru_base_text:
        pending = [] if strategy.mode == "skip" else list(pr_segments)
        return strategy, {}, pending

    plan = analyzer.plan_translation(
        ru_pr_text=ru_pr_text,
        en_current_text=en_current_text,
        ru_base_text=ru_base_text,
        pr_segments=pr_segments,
    )
    if not plan.seeded_translations:
        # Alignment failed → fall back to full
        fallback = TranslationStrategy(
            mode="full",
            reason="Differential seed empty (EN/base RU segment mismatch); full translation",
            config={"fallback_from_differential": True},
        )
        return fallback, {}, list(pr_segments)

    pending = [s for s in pr_segments if s.id in plan.pending_segment_ids]
    kept = len(plan.seeded_translations)
    logger.info(
        "Differential translation: seed %d segment(s), LLM %d segment(s) "
        "(magnitude hint in strategy=%s)",
        kept,
        len(pending),
        strategy.config.get("change_magnitude"),
    )
    return strategy, dict(plan.seeded_translations), pending
