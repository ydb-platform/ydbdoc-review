"""Unified file-level QA: round-trip align, heuristics, verdict (translate + verify)."""

from __future__ import annotations

import re
from typing import Literal

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import (
    normalize_target_segments_to_source,
)
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.validation.heuristics import (
    ClassifiedHeuristics,
    bump_verdict_for_blocking_heuristics,
)
from ydbdoc_review.validation.markers import (
    is_placeholder_only_text,
    non_variable_placeholders,
    placeholders_match,
)

FileVerdict = Literal["ok", "warnings", "blocked"]

# When RU/EN segment counts diverge this much, LCS pairing is unreliable even
# with placeholder gates (#48780 authentication.md 141 vs 90 → meaning mix-ups).
_MAX_PARTIAL_STRUCTURE_DRIFT = 0.25


def describe_segment_alignment_mismatch(
    source_segments: list[Segment],
    target_segments: list[Segment],
) -> str:
    """Human-readable segment alignment error with first divergence hint."""
    n_src = len(source_segments)
    n_tgt = len(target_segments)
    base = f"segment count mismatch: source {n_src} vs target {n_tgt}"

    for idx, (src, tgt) in enumerate(zip(source_segments, target_segments, strict=False)):
        if src.kind != tgt.kind or src.path != tgt.path:
            src_loc = " › ".join(src.path) if src.path else "(начало документа)"
            tgt_loc = " › ".join(tgt.path) if tgt.path else "(начало документа)"
            return (
                f"{base}; first structural diff at pair index {idx}: "
                f"RU `{src.id}` ({src.kind.value}, {src_loc}) vs "
                f"EN `{tgt.id}` ({tgt.kind.value}, {tgt_loc})"
            )

    if n_src > n_tgt:
        extra = source_segments[n_tgt]
        loc = " › ".join(extra.path) if extra.path else "(начало документа)"
        preview = re.sub(r"\s+", " ", extra.text)[:80]
        return (
            f"{base}; first extra RU segment `{extra.id}` "
            f"({extra.kind.value}, {loc}): «{preview}…»"
        )

    if n_tgt > n_src:
        extra = target_segments[n_src]
        loc = " › ".join(extra.path) if extra.path else "(начало документа)"
        preview = re.sub(r"\s+", " ", extra.text)[:80]
        return (
            f"{base}; first extra EN segment `{extra.id}` "
            f"({extra.kind.value}, {loc}): «{preview}…»"
        )

    return base


def _segment_structure_mismatch(
    source_segments: list[Segment],
    target_segments: list[Segment],
) -> bool:
    """True when counts differ or positional segment kinds diverge.

    Equal length alone is not enough: after RU-only rewrites the EN page can
    keep the same segment *count* while kinds drift (e.g. heading vs paragraph).
    Positional seed then pastes the wrong EN text into the RU AST (§6.163).
    """
    if len(target_segments) != len(source_segments):
        return True
    return any(
        src.kind != tgt.kind
        for src, tgt in zip(source_segments, target_segments, strict=True)
    )


def align_translations_from_target(
    source_segments: list[Segment],
    target_text: str,
) -> dict[str, str]:
    """Map source segment ids → texts from a rendered EN file (round-trip gate).

    Target segments are renumbered so each shared inline atom uses the source
    placeholder name. The critic and apply path then see consistent ``⟦Xn⟧``
    semantics across RU/EN — same name = same atom — instead of independent
    left-to-right numbering, which causes spurious "placeholder order
    mismatch" reports when word order shifts in translation.

    Requires matching segment **count and kinds** (positional). Kind drift with
    equal length rejects align so differential translation falls back to full.
    """
    target_segments_raw = extract_segments(parse_markdown(target_text))
    if _segment_structure_mismatch(source_segments, target_segments_raw):
        raise TranslationValidationError(
            describe_segment_alignment_mismatch(source_segments, target_segments_raw)
        )
    target_segments = normalize_target_segments_to_source(
        source_segments, target_segments_raw
    )
    return {
        src.id: tgt.text
        for src, tgt in zip(source_segments, target_segments, strict=True)
    }


def _segment_lcs_key(seg: Segment) -> tuple[object, ...]:
    """LCS key: kind + placeholder-letter signature (+ heading anchor) (§6.170 / §6.176).

    Kind-only LCS on heavily divergent pages (e.g. authentication.md 141 vs 90)
    pairs unrelated paragraphs and seeds EN text whose ``⟦…⟧`` set does not
    match the RU segment — reinsert then leaves literal markers in the file.

    Headings also key on explicit ``{#id}`` so LCS cannot seed «Precommit checks»
    onto «Заполните описание…» when anchors differ (#49040 / #48968).
    """
    ph_letters = tuple(
        p.placeholder[1] for p in seg.placeholders if len(p.placeholder) >= 2
    )
    if seg.kind == SegmentKind.HEADING:
        return (seg.kind, ph_letters, seg.heading_anchor)
    return (seg.kind, ph_letters)


_MISSING = object()


def partial_seed_is_trustworthy(
    src: Segment,
    en_text: str,
    *,
    target_heading_anchor: str | None | object = _MISSING,
) -> bool:
    """True when an LCS EN candidate is safe to reuse (§6.171 / §6.172 / §6.176).

    Placeholder multiset match alone is not enough: empty / ``⟦V⟧``-only
    paragraphs share the same signature and LCS still swaps meaning (LDAP
    steps ← IAM bullets, brute-force ↔ manual lockout on #48780).

    Placeholder-only sources (config table keys) must stay marker-only —
    never seed prose that wraps the same ``⟦C1⟧`` (#48785 ``default_group``).

    When ``target_heading_anchor`` is passed (partial LCS align), headings
    require matching explicit ``{#anchor}`` (or both absent).
    """
    if not placeholders_match(src.text, en_text):
        return False
    if is_placeholder_only_text(src.text):
        return is_placeholder_only_text(en_text)
    if (
        src.kind == SegmentKind.HEADING
        and target_heading_anchor is not _MISSING
        and src.heading_anchor != target_heading_anchor
    ):
        return False
    if non_variable_placeholders(src.text):
        return True
    if src.kind == SegmentKind.HEADING and len(src.text) <= 80:
        ratio = (len(en_text) + 1) / (len(src.text) + 1)
        return 0.45 <= ratio <= 2.2
    return False


def partial_align_translations_from_target(
    source_segments: list[Segment],
    target_text: str,
    *,
    require_trustworthy: bool = True,
) -> dict[str, str]:
    """Best-effort seed map when full structural align fails (§6.168–§6.176).

    LCS over ``(kind, placeholder-letter signature[, heading_anchor])``. Refuse
    the whole partial map when segment-count drift is high. Keep only
    trustworthy pairs (non-V placeholder fingerprint, or short heading with
    matching ``{#id}`` and length parity) when ``require_trustworthy`` is True.

    When False (§6.184 / #45667), keep any LCS pair whose placeholders match
    (and placeholder-only cells stay marker-only). Used to reuse EN for
    *unchanged* RU segments without re-LLM.
    """
    target_segments_raw = extract_segments(parse_markdown(target_text))
    n_src = len(source_segments)
    n_tgt = len(target_segments_raw)
    if n_src == 0 or n_tgt == 0:
        return {}

    drift = abs(n_src - n_tgt) / max(n_src, n_tgt)
    if drift > _MAX_PARTIAL_STRUCTURE_DRIFT:
        return {}

    src_keys = [_segment_lcs_key(s) for s in source_segments]
    tgt_keys = [_segment_lcs_key(t) for t in target_segments_raw]
    dp = [[0] * (n_tgt + 1) for _ in range(n_src + 1)]
    for i in range(1, n_src + 1):
        for j in range(1, n_tgt + 1):
            if src_keys[i - 1] == tgt_keys[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    pairs: list[tuple[int, int]] = []
    i, j = n_src, n_tgt
    while i > 0 and j > 0:
        if src_keys[i - 1] == tgt_keys[j - 1] and dp[i][j] == dp[i - 1][j - 1] + 1:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()

    seeded: dict[str, str] = {}
    for si, ti in pairs:
        src = source_segments[si]
        tgt_seg = target_segments_raw[ti]
        tgt_one = normalize_target_segments_to_source(
            [src], [tgt_seg]
        )[0]
        if require_trustworthy:
            if not partial_seed_is_trustworthy(
                src,
                tgt_one.text,
                target_heading_anchor=tgt_seg.heading_anchor,
            ):
                continue
        else:
            if not placeholders_match(src.text, tgt_one.text):
                continue
            if is_placeholder_only_text(src.text) and not is_placeholder_only_text(
                tgt_one.text
            ):
                continue
        seeded[src.id] = tgt_one.text
    return seeded


def gate_round_trip(
    segments: list[Segment],
    target_text: str,
) -> tuple[dict[str, str], str | None]:
    """Return (translations, alignment_error). Error text is set when gate fails."""
    try:
        return align_translations_from_target(segments, target_text), None
    except TranslationValidationError as exc:
        return {}, str(exc)


def compose_file_verdict(
    *,
    critic_verdict: FileVerdict,
    alignment_error: str | None,
    heuristics: ClassifiedHeuristics,
    manual_actions: bool,
) -> FileVerdict:
    """Single verdict rule for doc_translate and doc_verify."""
    if alignment_error:
        return "blocked"
    verdict = critic_verdict
    verdict = bump_verdict_for_blocking_heuristics(verdict, heuristics.blocking)
    if heuristics.warnings and verdict == "ok":
        verdict = "warnings"
    if manual_actions and verdict == "ok":
        verdict = "warnings"
    return verdict
