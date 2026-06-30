"""Tests for spurious placeholder drift filtering."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import InlineCode, InlineLink, InlineText
from ydbdoc_review.segmentation.types import ProtectedInline, Segment, SegmentKind
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.validation.markers import (
    cross_lang_placeholder_drift_only,
    variable_placeholder_drift_only,
)
from ydbdoc_review.validation.placeholder_drift import (
    critic_issue_dedupe_key,
    drop_spurious_placeholder_issues,
    exclude_skipped_issues,
    filter_critic_response,
    is_spurious_code_literal_equivalent_issue,
    is_spurious_cross_lang_placeholder_issue,
    is_spurious_hallucinated_link_issue,
    is_spurious_hallucinated_substitution_issue,
    is_spurious_locale_url_issue,
    is_spurious_null_literal_issue,
    is_spurious_phantom_marker_swap_issue,
    is_spurious_plain_text_wrapping_issue,
)


def _segment(seg_id: str, text: str, *, placeholders: list | None = None) -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text=text,
        placeholders=placeholders or [],
        ast_path=[0],
    )


def test_variable_placeholder_drift_only_allows_one_missing_v():
    ru = (
        "⟦V1⟧ text ⟦V2⟧ more [link](⟦U1⟧) ⟦V3⟧ topics ⟦V4⟧ end"
    )
    en = (
        "⟦V1⟧ text ⟦V2⟧ more [link](⟦U1⟧) ⟦V3⟧ end"
    )
    assert variable_placeholder_drift_only(ru, en)


def test_variable_placeholder_drift_rejects_code_mismatch():
    ru = "Use ⟦C1⟧ and ⟦V1⟧"
    en = "Use ⟦C2⟧ and ⟦V1⟧"
    assert not variable_placeholder_drift_only(ru, en)


def test_drop_spurious_placeholder_issues_streaming_query_style():
    """Regression #41206: critic placeholder corruption on {{ ydb-short-name }} drift."""
    ru = (
        "⟦V1⟧ реализует потоковую обработку. Интеграция в ⟦V2⟧ позволяет получать "
        "данные из [топиков](⟦U1⟧) ⟦V3⟧, записывать в ⟦V4⟧."
    )
    en = (
        "⟦V1⟧ implements stream processing. Integration into ⟦V2⟧ lets you ingest "
        "data from [topics](⟦U1⟧), write inside ⟦V3⟧."
    )
    seg = _segment("s0004", ru)
    issue = CriticIssueOut(
        segment_id="s0004",
        severity="warning",
        category="placeholder corruption",
        comment="Missing placeholder ⟦V4⟧",
        suggested_text="broken",
    )
    filtered = drop_spurious_placeholder_issues([issue], [seg], {"s0004": en})
    assert filtered == []


def test_filter_critic_response_clears_verdict():
    ru = "⟦V1⟧ one ⟦V2⟧ two"
    en = "⟦V1⟧ one two"
    seg = _segment("s1", ru)
    response = CriticResponse(
        verdict="warnings",
        issues=[
            CriticIssueOut(
                segment_id="s1",
                severity="warning",
                category="placeholder corruption",
                comment="drift",
                suggested_text=None,
            )
        ],
    )
    out = filter_critic_response(response, [seg], {"s1": en})
    assert out is not None
    assert out.issues == []
    assert out.verdict == "ok"


def test_cross_lang_reorder_issue_dropped():
    """§6.56: same atom multiset, reorder comment — spurious after align."""
    ru = "к таблице ⟦C1⟧ колонку ⟦C2⟧ с типом ⟦C3⟧"
    en = "column ⟦C2⟧ with type ⟦C3⟧ to ⟦C1⟧ table"
    assert cross_lang_placeholder_drift_only(ru, en)
    seg = _segment("s0013", ru)
    issue = CriticIssueOut(
        segment_id="s0013",
        severity="blocked",
        category="placeholder corruption",
        comment="Placeholder order changed: source C1,C2,C3; translation C2,C3,C1",
        suggested_text="broken",
    )
    assert is_spurious_cross_lang_placeholder_issue(issue, seg, en)
    filtered = drop_spurious_placeholder_issues([issue], [seg], {"s0013": en})
    assert filtered == []


def test_cross_lang_real_mismatch_not_dropped():
    ru = "Use ⟦C1⟧ and ⟦C2⟧"
    en = "Use ⟦C1⟧ only"
    seg = _segment("s1", ru)
    issue = CriticIssueOut(
        segment_id="s1",
        severity="blocked",
        category="placeholder corruption",
        comment="Missing ⟦C2⟧",
        suggested_text=None,
    )
    assert not is_spurious_cross_lang_placeholder_issue(issue, seg, en)
    filtered = drop_spurious_placeholder_issues([issue], [seg], {"s1": en})
    assert filtered == [issue]


def test_atom_map_marker_id_noise_dropped():
    """§6.57: multiset matches with reorder — atom_map marker-id noise dropped."""
    ru = "Use ⟦C1⟧ then ⟦C2⟧ size"
    en = "Use ⟦C2⟧ size then ⟦C1⟧"
    seg = _segment("s0002", ru)
    issue = CriticIssueOut(
        segment_id="s0002",
        severity="blocked",
        category="placeholder corruption",
        comment="⟦U2⟧ not in atom_map",
        suggested_text=None,
    )
    assert is_spurious_cross_lang_placeholder_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0002": en}) == []


def test_identical_placeholder_sequence_mapping_not_dropped():
    """§6.59 #43365: same marker ids, wrong prose roles — keep for critic apply."""
    ru = "register in ⟦C1⟧ both ⟦C2⟧ and ⟦C3⟧"
    en = "register both ⟦C1⟧ and ⟦C2⟧ in ⟦C3⟧"
    seg = _segment("s0109", ru)
    issue = CriticIssueOut(
        segment_id="s0109",
        severity="blocked",
        category="placeholder order",
        comment="Placeholder order swapped: source C2,C3 in C1",
        suggested_text="register both ⟦C2⟧ and ⟦C3⟧ in ⟦C1⟧",
    )
    assert not is_spurious_cross_lang_placeholder_issue(issue, seg, en)
    filtered = drop_spurious_placeholder_issues([issue], [seg], {"s0109": en})
    assert filtered == [issue]


def test_locale_wikipedia_noise_dropped():
    ru = "Read [Индекс](⟦U1⟧) for details"
    en = "Read [Index](⟦U1⟧) for details"
    seg = _segment(
        "s0002",
        ru,
        placeholders=[
            ProtectedInline(
                placeholder="⟦U1⟧",
                node=InlineLink(
                    href="https://ru.wikipedia.org/wiki/Database_index",
                    children=[InlineText(content="Индекс")],
                ),
            )
        ],
    )
    issue = CriticIssueOut(
        segment_id="s0002",
        severity="blocked",
        category="placeholder corruption",
        comment="Locale mismatch: atom_map shows ru.wikipedia but EN should use en.wikipedia",
        suggested_text=None,
    )
    assert is_spurious_locale_url_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0002": en}) == []


def test_null_literal_ping_pong_dropped():
    ru = "Column may be NULL or ⟦C1⟧"
    en = "Column may be `NULL` or ⟦C1⟧"
    seg = _segment(
        "s0010",
        ru,
        placeholders=[ProtectedInline(placeholder="⟦C1⟧", node=InlineCode(content="NOT NULL"))],
    )
    issue = CriticIssueOut(
        segment_id="s0010",
        severity="blocked",
        category="placeholder corruption",
        comment="NULL should be ⟦C1⟧ placeholder, not literal",
        suggested_text="broken",
    )
    assert is_spurious_null_literal_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0010": en}) == []


def test_vacuum_code_literal_equivalent_dropped():
    ru = "Run ⟦C1⟧ to reclaim space"
    en = "Run VACUUM to reclaim space"
    seg = _segment(
        "s0013",
        ru,
        placeholders=[ProtectedInline(placeholder="⟦C1⟧", node=InlineCode(content="VACUUM"))],
    )
    issue = CriticIssueOut(
        segment_id="s0013",
        severity="blocked",
        category="placeholder corruption",
        comment="VACUUM should use ⟦C1⟧ placeholder from source",
        suggested_text="Run ⟦C1⟧ to reclaim space",
    )
    assert is_spurious_code_literal_equivalent_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0013": en}) == []


def test_hallucinated_substitution_dropped():
    ru = "Set ⟦C1⟧ parameter"
    en = "Set AUTO_PARTITIONING_MIN_PARTITIONS_COUNT parameter"
    seg = _segment("s0169", ru)
    issue = CriticIssueOut(
        segment_id="s0169",
        severity="blocked",
        category="placeholder corruption",
        comment="AUTO_PARTITIONING_MIN_PARTITIONS_COUNT was replaced by ⟦C1⟧",
        suggested_text="Set ⟦C1⟧ parameter",
    )
    assert is_spurious_hallucinated_substitution_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0169": en}) == []


def test_hallucinated_link_dropped_when_source_has_no_url_atom():
    ru = "**Join operations** — algorithm is used."
    en = "**Join operations** — the [Grace Hash Join](⟦U1⟧) algorithm is used."
    seg = _segment("s0028", ru)
    issue = CriticIssueOut(
        segment_id="s0028",
        severity="warning",
        category="content change",
        comment="Added a link '[Grace Hash Join](⟦U1⟧)' that was not present in the source.",
        suggested_text=ru,
    )
    assert is_spurious_hallucinated_link_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0028": en}) == []


def test_exclude_skipped_issues_dedupes_verify_echo():
    skipped = CriticIssueOut(
        segment_id="s0013",
        severity="blocked",
        category="placeholder corruption",
        comment="order change rejected",
        suggested_text="broken",
    )
    echo = CriticIssueOut(
        segment_id="s0013",
        severity="blocked",
        category="placeholder corruption",
        comment="order change rejected",
        suggested_text="broken",
    )
    real = CriticIssueOut(
        segment_id="s0015",
        severity="warning",
        category="terminology",
        comment="missing link",
        suggested_text=None,
    )
    out = exclude_skipped_issues([echo, real], [skipped])
    assert out == [real]
    assert critic_issue_dedupe_key(skipped) == critic_issue_dedupe_key(echo)


def test_filter_critic_response_excludes_skipped():
    ru = "⟦V1⟧ one ⟦V2⟧ two"
    en = "⟦V1⟧ one two"
    seg = _segment("s1", ru)
    skipped = CriticIssueOut(
        segment_id="s1",
        severity="blocked",
        category="placeholder corruption",
        comment="drift",
        suggested_text="x",
    )
    response = CriticResponse(
        verdict="blocked",
        issues=[skipped],
    )
    out = filter_critic_response(response, [seg], {"s1": en}, skipped=[skipped])
    assert out is not None
    assert out.issues == []
    assert out.verdict == "ok"


def test_plain_text_index_name_wrapping_dropped():
    """§6.61 #43860: RU plain Index12, EN inline code — not placeholder corruption."""
    ru = (
        "⟦C1⟧ — должен быть выбран Index12, так как при его выборе "
        "в получающемся диапазоне ⟦C2⟧ получится длина точечного префикса — 2."
    )
    en = (
        "⟦C1⟧ — ⟦C2⟧ should be selected, because when it is selected, "
        "the resulting range ⟦C3⟧ yields a point prefix length of 2."
    )
    seg = _segment("s0046", ru)
    issue = CriticIssueOut(
        segment_id="s0046",
        severity="blocked",
        category="placeholder corruption",
        comment=(
            "Introduced placeholder ⟦C3⟧ not present in atom_map; "
            "source had plain text 'Index12'"
        ),
        suggested_text=None,
    )
    assert is_spurious_plain_text_wrapping_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0046": en}) == []


def test_phantom_marker_swap_dropped_when_sequences_match():
    """§6.61 #43860: critic claims U1→U2 but EN still has ⟦U1⟧."""
    ru = (
        "use [распределенных транзакций](⟦U1⟧) even for single partition"
    )
    en = (
        "use [distributed transactions](⟦U1⟧) even for single partition"
    )
    seg = _segment("s0069", ru)
    issue = CriticIssueOut(
        segment_id="s0069",
        severity="blocked",
        category="placeholder corruption",
        comment=(
            "Placeholder ⟦U1⟧ in source text was replaced with ⟦U2⟧ in translation, "
            "but atom_map only defines ⟦U1⟧"
        ),
        suggested_text=None,
    )
    assert is_spurious_phantom_marker_swap_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0069": en}) == []


def test_phantom_marker_swap_dropped_for_translated_formula_slot():
    """Regression #44268: aligned EN keeps ⟦C1⟧ after gate_round_trip."""
    ru = (
        "Начальное количество партиций: для базовой оценки числа партиций "
        "можно использовать формулу ⟦C1⟧. Это позволит максимально утилизировать "
        "ресурсы кластера при выполнении параллельных запросов."
    )
    en = (
        "Initial number of partitions: for a basic estimate of the number of "
        "partitions, you can use the formula ⟦C1⟧. This will maximize cluster "
        "resource utilization when executing parallel queries."
    )
    seg = _segment("s0064", ru)
    issue = CriticIssueOut(
        segment_id="s0064",
        severity="warning",
        category="placeholder corruption",
        comment=(
            "Placeholder ⟦C1⟧ in source was incorrectly changed to ⟦C2⟧ in "
            "translation. The atom_map defines only ⟦C1⟧ for this segment."
        ),
        suggested_text=en,
    )
    assert is_spurious_phantom_marker_swap_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0064": en}) == []


def test_plain_text_wrapping_not_dropped_when_identifier_missing():
    """Keep issue when EN drops a plain identifier entirely."""
    ru = "⟦C1⟧ — должен быть выбран Index12, так как ⟦C2⟧"
    en = "⟦C1⟧ — should be selected, because ⟦C2⟧"
    seg = _segment("s0046", ru)
    issue = CriticIssueOut(
        segment_id="s0046",
        severity="blocked",
        category="placeholder corruption",
        comment="Introduced placeholder ⟦C3⟧; source had plain text 'Index12'",
        suggested_text=None,
    )
    assert not is_spurious_plain_text_wrapping_issue(issue, seg, en)
    assert drop_spurious_placeholder_issues([issue], [seg], {"s0046": en}) == [issue]

