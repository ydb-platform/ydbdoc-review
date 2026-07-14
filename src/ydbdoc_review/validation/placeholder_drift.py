"""Filter spurious critic issues about placeholder drift across RU/EN."""

from __future__ import annotations

import logging
import re

from ydbdoc_review.parsing.ast_types import InlineLink
from ydbdoc_review.segmentation.placeholder_align import segment_atom_legend
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse, CriticVerdict
from ydbdoc_review.validation.markers import (
    cross_lang_placeholder_drift_only,
    extract_placeholders,
    placeholders_match,
    variable_placeholder_drift_only,
)

logger = logging.getLogger(__name__)

_PLACEHOLDER_ISSUE = re.compile(r"placeholder", re.IGNORECASE)
_REORDER_ISSUE = re.compile(
    r"order|reorder|renumber|changed to ⟦|mapping|atom_map|not in atom|"
    r"not defined|marker id|wrong marker",
    re.IGNORECASE,
)
_LOCALE_ISSUE = re.compile(
    r"locale|wikipedia|ru\.wikipedia|en\.wikipedia",
    re.IGNORECASE,
)
_NULL_ISSUE = re.compile(r"\bnull\b", re.IGNORECASE)
_SUBSTITUTION_CLAIM = re.compile(
    r"([A-Z][A-Z0-9_]{3,})\s+(?:was\s+)?(?:replaced|substituted)\s+(?:by|with)\s+⟦",
    re.IGNORECASE,
)
_WIKIPEDIA_URL = re.compile(r"wikipedia\.org", re.IGNORECASE)
_NULL_LITERAL = re.compile(r"\bnull\b", re.IGNORECASE)
_PLAIN_TEXT_ATOM = re.compile(
    r"source had plain text ['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_PHANTOM_MARKER_SWAP = re.compile(
    r"⟦[CLIHVTUS]\d+⟧.*(?:replaced with|changed to|used in translated).*⟦[CLIHVTUS]\d+⟧|"
    r"atom_map only defines",
    re.IGNORECASE,
)
_HALLUCINATED_LINK_ISSUE = re.compile(
    r"not present in the source|extra placeholder|introduces an extra|"
    r"added a link|content change",
    re.IGNORECASE,
)


def critic_issue_dedupe_key(issue: CriticIssueOut) -> tuple:
    """Stable key for matching the same critic item across apply / verify / report."""
    return (
        issue.segment_id,
        issue.category.lower(),
        issue.comment.strip(),
        issue.suggested_text,
    )


def exclude_skipped_issues(
    issues: list[CriticIssueOut],
    skipped: list[CriticIssueOut],
) -> list[CriticIssueOut]:
    """Drop verify issues that apply already rejected (avoid double-listing in report)."""
    if not skipped:
        return issues
    skipped_keys = {critic_issue_dedupe_key(i) for i in skipped}
    return [i for i in issues if critic_issue_dedupe_key(i) not in skipped_keys]


def is_spurious_variable_placeholder_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop critic noise when only ``{{ ydb-short-name }}`` placement/count drifts."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    return variable_placeholder_drift_only(segment.text, translation)


def is_spurious_cross_lang_placeholder_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop reorder/renumber noise when RU/EN share the same non-``⟦V⟧`` multiset."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    # Identical placeholder sequence — critic may flag wrong prose roles (§6.59);
    # keep the issue so ``apply_critic_fixes`` can apply ``suggested_text``.
    if extract_placeholders(segment.text) == extract_placeholders(translation):
        return False
    if not _atom_multiset_matches(segment, translation):
        return False
    haystack = f"{issue.category} {issue.comment}"
    return bool(_REORDER_ISSUE.search(haystack))


def _atom_multiset_matches(segment: Segment | None, translation: str | None) -> bool:
    if segment is None or translation is None:
        return False
    return cross_lang_placeholder_drift_only(segment.text, translation) or placeholders_match(
        segment.text, translation
    )


def _references_null(text: str, segment: Segment | None) -> bool:
    if _NULL_LITERAL.search(text):
        return True
    if segment is None:
        return False
    legend = segment_atom_legend(segment)
    return any(entry.endswith(":null") or entry == "code:null" for entry in legend.values())


def is_spurious_null_literal_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop NULL ↔ ``⟦C{n}⟧`` ping-pong when both sides reference NULL."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    haystack = f"{issue.category} {issue.comment}"
    if not _NULL_ISSUE.search(haystack):
        return False
    return _references_null(segment.text, segment) and _references_null(translation, segment)


def _segment_has_wikipedia_link(segment: Segment) -> bool:
    for placeholder in segment.placeholders:
        node = placeholder.node
        if isinstance(node, InlineLink) and node.href and _WIKIPEDIA_URL.search(node.href):
            return True
    return False


def is_spurious_locale_url_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop locale noise when EN uses en.wikipedia and RU uses ru.wikipedia for same atom."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    haystack = f"{issue.category} {issue.comment}"
    if not _LOCALE_ISSUE.search(haystack):
        return False
    if not _atom_multiset_matches(segment, translation):
        return False
    combined = f"{segment.text} {translation}"
    return _segment_has_wikipedia_link(segment) or bool(_WIKIPEDIA_URL.search(combined))


def is_spurious_code_literal_equivalent_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop when critic flags ``VACUUM`` vs ``⟦C1⟧`` but both sides carry the same code atom."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    legend = segment_atom_legend(segment)
    code_atoms = {
        entry.split(":", 1)[1]
        for entry in legend.values()
        if entry.startswith("code:")
    }
    if not code_atoms:
        return False
    haystack = f"{issue.category} {issue.comment}"
    for atom in code_atoms:
        if len(atom) < 2 or " " in atom:
            continue
        if atom not in haystack and atom.lower() not in haystack.lower():
            continue
        bare_in_translation = re.search(rf"\b{re.escape(atom)}\b", translation) is not None
        marked_in_translation = any(
            ph in translation for ph in legend if legend[ph] == f"code:{atom}"
        )
        bare_in_source = atom in segment.text
        marked_in_source = any(
            ph in segment.text for ph in legend if legend[ph] == f"code:{atom}"
        )
        if (bare_in_translation or marked_in_translation) and (
            bare_in_source or marked_in_source
        ):
            return True
    return False


def is_spurious_plain_text_wrapping_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop when critic flags plain prose ids but EN wrapped them in inline code."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    haystack = f"{issue.category} {issue.comment}"
    match = _PLAIN_TEXT_ATOM.search(haystack)
    if match is None or "introduced placeholder" not in haystack.lower():
        return False
    ident = match.group(1)
    if not re.search(rf"\b{re.escape(ident)}\b", segment.text):
        return False
    if re.search(rf"`{re.escape(ident)}`|\b{re.escape(ident)}\b", translation):
        return False
    src_ph = extract_placeholders(segment.text)
    tgt_ph = extract_placeholders(translation)
    return len(tgt_ph) > len(src_ph)


def is_spurious_phantom_marker_swap_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop atom_map marker-id noise when RU/EN share the same placeholder sequence."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    haystack = f"{issue.category} {issue.comment}"
    if not _PHANTOM_MARKER_SWAP.search(haystack):
        return False
    if extract_placeholders(segment.text) == extract_placeholders(translation):
        return True
    from ydbdoc_review.validation.markers import cross_lang_placeholder_drift_only

    return cross_lang_placeholder_drift_only(segment.text, translation)


def is_spurious_hallucinated_link_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop when critic flags a new ``[text](⟦U⟧)`` but the source had no URL atom."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if any(p.placeholder[1] == "U" for p in segment.placeholders):
        return False
    if not re.search(r"\]\(⟦U\d+⟧\)", translation):
        return False
    haystack = f"{issue.category} {issue.comment}"
    return bool(_HALLUCINATED_LINK_ISSUE.search(haystack)) or bool(
        _PLACEHOLDER_ISSUE.search(issue.category)
    )


def is_spurious_hallucinated_substitution_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop when critic claims a literal was replaced by a placeholder that is not in EN."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    haystack = f"{issue.category} {issue.comment}"
    match = _SUBSTITUTION_CLAIM.search(haystack)
    if match is None:
        return False
    ident = match.group(1)
    if ident not in translation:
        return False
    claimed_ph = re.search(r"⟦[CLIHVTUS]\d+⟧", haystack)
    if claimed_ph is None:
        return ident in translation
    return claimed_ph.group(0) not in translation


def drop_spurious_placeholder_issues(
    issues: list[CriticIssueOut],
    segments: list[Segment],
    translations: dict[str, str],
) -> list[CriticIssueOut]:
    by_id = {s.id: s for s in segments}
    out: list[CriticIssueOut] = []
    for issue in issues:
        seg = by_id.get(issue.segment_id) if issue.segment_id else None
        trans = translations.get(issue.segment_id) if issue.segment_id else None
        if is_spurious_variable_placeholder_issue(issue, seg, trans):
            logger.info(
                "Ignoring spurious V-placeholder critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_cross_lang_placeholder_issue(issue, seg, trans):
            logger.info(
                "Ignoring spurious cross-lang placeholder critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_null_literal_issue(issue, seg, trans):
            logger.info(
                "Ignoring spurious NULL literal critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_locale_url_issue(issue, seg, trans):
            logger.info(
                "Ignoring spurious locale URL critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_code_literal_equivalent_issue(issue, seg, trans):
            logger.info(
                "Ignoring spurious code/literal equivalent critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_hallucinated_substitution_issue(issue, seg, trans):
            logger.info(
                "Ignoring hallucinated substitution critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_hallucinated_link_issue(issue, seg, trans):
            logger.info(
                "Ignoring hallucinated link critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_plain_text_wrapping_issue(issue, seg, trans):
            logger.info(
                "Ignoring plain-text wrapping critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_phantom_marker_swap_issue(issue, seg, trans):
            logger.info(
                "Ignoring phantom marker swap critic issue for %s",
                issue.segment_id,
            )
            continue
        out.append(issue)
    return out


def _verdict_from_issues(issues: list[CriticIssueOut]) -> CriticVerdict:
    if not issues:
        return "ok"
    if any(i.severity == "blocked" for i in issues):
        return "blocked"
    return "warnings"


def filter_critic_response(
    response: CriticResponse | None,
    segments: list[Segment],
    translations: dict[str, str],
    *,
    skipped: list[CriticIssueOut] | None = None,
) -> CriticResponse | None:
    """Remove spurious placeholder issues, skipped duplicates, and refresh verdict."""
    if response is None:
        return None
    filtered = drop_spurious_placeholder_issues(response.issues, segments, translations)
    filtered = exclude_skipped_issues(filtered, skipped or [])
    if filtered == response.issues:
        return response
    return CriticResponse(verdict=_verdict_from_issues(filtered), issues=filtered)
