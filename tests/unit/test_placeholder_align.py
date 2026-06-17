"""Tests for cross-language placeholder renumbering used by doc_verify."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import (
    InlineCode,
    InlineImage,
    InlineLink,
    InlineVariable,
)
from ydbdoc_review.segmentation.placeholder_align import (
    normalize_target_segments_to_source,
)
from ydbdoc_review.segmentation.types import ProtectedInline, Segment, SegmentKind


def _seg(seg_id: str, text: str, placeholders: list[ProtectedInline]) -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text=text,
        placeholders=placeholders,
        ast_path=[0],
    )


def _ph(name: str, node) -> ProtectedInline:
    return ProtectedInline(placeholder=name, node=node)


def test_columns_md_s0013_reorder():
    """RU and EN agree on atoms but disagree on left-to-right placeholder names."""
    src = _seg(
        "s0013",
        "к таблице ⟦C1⟧ колонку ⟦C2⟧ с типом ⟦C3⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="episodes")),
            _ph("⟦C2⟧", InlineCode(content="views")),
            _ph("⟦C3⟧", InlineCode(content="Uint64")),
        ],
    )
    tgt = _seg(
        "s0013",
        "column ⟦C1⟧ with type ⟦C2⟧ to ⟦C3⟧ table",
        [
            _ph("⟦C1⟧", InlineCode(content="views")),
            _ph("⟦C2⟧", InlineCode(content="Uint64")),
            _ph("⟦C3⟧", InlineCode(content="episodes")),
        ],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    # EN now uses RU numbering: views=⟦C2⟧, Uint64=⟦C3⟧, episodes=⟦C1⟧.
    assert normalized.text == "column ⟦C2⟧ with type ⟦C3⟧ to ⟦C1⟧ table"
    new_map = {p.placeholder: p.node.content for p in normalized.placeholders}
    assert new_map == {"⟦C2⟧": "views", "⟦C3⟧": "Uint64", "⟦C1⟧": "episodes"}


def test_no_op_when_numbering_already_matches():
    src = _seg(
        "s1", "Use ⟦C1⟧ and ⟦C2⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="foo")),
            _ph("⟦C2⟧", InlineCode(content="bar")),
        ],
    )
    tgt = _seg(
        "s1", "Use ⟦C1⟧ and ⟦C2⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="foo")),
            _ph("⟦C2⟧", InlineCode(content="bar")),
        ],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    assert normalized is tgt


def test_url_locale_prefix_is_normalized():
    src = _seg(
        "s1", "See [doc](⟦U1⟧)",
        [_ph("⟦U1⟧", InlineLink(href="/ru/docs/foo", children=[]))],
    )
    tgt = _seg(
        "s1", "See [doc](⟦U1⟧)",
        [_ph("⟦U1⟧", InlineLink(href="/en/docs/foo", children=[]))],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    assert {p.placeholder for p in normalized.placeholders} == {"⟦U1⟧"}


def test_relative_doc_paths_match_by_basename():
    """Mirror RU/EN links with different ``../`` depth and anchors."""
    src = _seg(
        "s1", "[LSM](⟦U1⟧)",
        [
            _ph(
                "⟦U1⟧",
                InlineLink(
                    href="../../query_execution/mvcc.md#organizaciya-hraneniya",
                    children=[],
                ),
            )
        ],
    )
    tgt = _seg(
        "s1", "[LSM](⟦U1⟧)",
        [
            _ph(
                "⟦U1⟧",
                InlineLink(
                    href="../../../concepts/query_execution/mvcc.md#how-ydb-stores",
                    children=[],
                ),
            )
        ],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    assert normalized.placeholders[0].placeholder == "⟦U1⟧"


def test_null_code_matches_case_insensitive():
    src = _seg(
        "s1", "allow NULL values",
        [],
    )
    tgt = _seg(
        "s1", "allow ⟦C1⟧ values",
        [_ph("⟦C1⟧", InlineCode(content="NULL"))],
    )
    # NULL in prose on src side — no placeholder; tgt-only C1 stays.
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    assert normalized is tgt

    src2 = _seg(
        "s2", "allow ⟦C1⟧ values",
        [_ph("⟦C1⟧", InlineCode(content="NULL"))],
    )
    tgt2 = _seg(
        "s2", "allow ⟦C1⟧ values",
        [_ph("⟦C1⟧", InlineCode(content="null"))],
    )
    [normalized2] = normalize_target_segments_to_source([src2], [tgt2])
    assert normalized2.placeholders[0].placeholder == "⟦C1⟧"


def test_segment_atom_legend():
    from ydbdoc_review.segmentation.placeholder_align import segment_atom_legend

    seg = _seg(
        "s1", "⟦C1⟧",
        [_ph("⟦C1⟧", InlineCode(content="episodes"))],
    )
    assert segment_atom_legend(seg) == {"⟦C1⟧": "code:episodes"}


def test_yfm_variable_matches_by_name():
    src = _seg(
        "s1", "Backend is ⟦V1⟧",
        [_ph("⟦V1⟧", InlineVariable(name="backend_name", raw="{{ backend_name }}"))],
    )
    tgt = _seg(
        "s1", "⟦V1⟧ is the backend",
        [_ph("⟦V1⟧", InlineVariable(name="backend_name", raw="{{ backend_name }}"))],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    assert normalized.placeholders[0].placeholder == "⟦V1⟧"


def test_duplicate_atoms_match_in_order():
    """Same content appearing twice in src/tgt is paired left-to-right."""
    src = _seg(
        "s1", "Use ⟦C1⟧ then ⟦C2⟧ then ⟦C3⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="foo")),
            _ph("⟦C2⟧", InlineCode(content="bar")),
            _ph("⟦C3⟧", InlineCode(content="foo")),
        ],
    )
    tgt = _seg(
        "s1", "First ⟦C1⟧, then ⟦C2⟧, last ⟦C3⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="foo")),  # → src ⟦C1⟧
            _ph("⟦C2⟧", InlineCode(content="foo")),  # → src ⟦C3⟧
            _ph("⟦C3⟧", InlineCode(content="bar")),  # → src ⟦C2⟧
        ],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    new_map = {p.placeholder: p.node.content for p in normalized.placeholders}
    # First foo in tgt → C1 (first foo in src). Second foo → C3 (second foo in src).
    # bar → C2.
    assert new_map == {"⟦C1⟧": "foo", "⟦C3⟧": "foo", "⟦C2⟧": "bar"}


def test_unmatched_target_atom_gets_fresh_name():
    """An EN-only atom must not clash with any source name."""
    src = _seg(
        "s1", "Use ⟦C1⟧ and ⟦C2⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="foo")),
            _ph("⟦C2⟧", InlineCode(content="bar")),
        ],
    )
    tgt = _seg(
        "s1", "Use ⟦C1⟧ and ⟦C2⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="foo")),   # → ⟦C1⟧
            _ph("⟦C2⟧", InlineCode(content="extra")), # EN-only, must rename
        ],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    names = [p.placeholder for p in normalized.placeholders]
    # foo keeps ⟦C1⟧; the extra atom must NOT take ⟦C2⟧ (bar in src).
    assert names[0] == "⟦C1⟧"
    assert names[1] != "⟦C2⟧"
    assert names[1].startswith("⟦C")


def test_image_atoms_match_by_src():
    src = _seg(
        "s1", "![ru-alt](⟦S1⟧)",
        [_ph("⟦S1⟧", InlineImage(src="img/diagram.svg", alt="ru-alt"))],
    )
    tgt = _seg(
        "s1", "![en-alt](⟦S1⟧)",
        [_ph("⟦S1⟧", InlineImage(src="img/diagram.svg", alt="en-alt"))],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    assert normalized.placeholders[0].placeholder == "⟦S1⟧"


def test_count_mismatch_returns_target_unchanged():
    src = _seg("s1", "⟦C1⟧", [_ph("⟦C1⟧", InlineCode(content="foo"))])
    tgt = _seg(
        "s1", "⟦C1⟧ ⟦C2⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="foo")),
            _ph("⟦C2⟧", InlineCode(content="bar")),
        ],
    )
    # Segment-count mismatch is a separate alignment problem; alignment caller
    # decides what to do. Per-segment differences in placeholder count are fine.
    result = normalize_target_segments_to_source([src, src], [tgt])
    assert result == [tgt]


def test_swap_renumbering_is_atomic():
    """⟦C1⟧→⟦C2⟧ and ⟦C2⟧→⟦C1⟧ at once must not double-apply."""
    src = _seg(
        "s1", "⟦C1⟧ then ⟦C2⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="a")),
            _ph("⟦C2⟧", InlineCode(content="b")),
        ],
    )
    tgt = _seg(
        "s1", "⟦C1⟧ then ⟦C2⟧",
        [
            _ph("⟦C1⟧", InlineCode(content="b")),  # swap
            _ph("⟦C2⟧", InlineCode(content="a")),
        ],
    )
    [normalized] = normalize_target_segments_to_source([src], [tgt])
    assert normalized.text == "⟦C2⟧ then ⟦C1⟧"
    new_map = {p.placeholder: p.node.content for p in normalized.placeholders}
    assert new_map == {"⟦C2⟧": "b", "⟦C1⟧": "a"}
