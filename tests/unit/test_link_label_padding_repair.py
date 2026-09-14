"""Safe finalization of model-added padding inside Markdown link labels."""

# ruff: noqa: RUF001 - exact Russian production-shaped source fixtures.

from __future__ import annotations

from ydbdoc_review.harness.render import finalize_en_target
from ydbdoc_review.validation.editorial import check_en_editorial
from ydbdoc_review.validation.link_label_padding import (
    repair_markdown_link_label_padding,
)


def test_pr52983_s0052_repairs_model_added_link_label_padding() -> None:
    source = (
        "| `use_tls` | Определяет, будет ли предпринята попытка установить TLS-соединение "
        "с [использованием запроса `StartTls`](../../security/authentication.md#starttls) "
        "после установления TCP-соединения. |\n"
    )
    candidate = (
        "| `use_tls` | Determines whether an attempt will be made to establish a TLS "
        "connection with [ using the `StartTls` request ]"
        "(../../security/authentication.md#starttls) after establishing a TCP connection. |\n"
    )
    expected = candidate.replace(
        "[ using the `StartTls` request ]", "[using the `StartTls` request]"
    )

    finalized = finalize_en_target(candidate, source)

    assert finalized == expected
    assert check_en_editorial(finalized) == []


def test_repair_preserves_nested_brackets_escapes_entities_and_destination_bytes() -> None:
    source = r'[метка [со скобками] \] &amp; `код`](<path with spaces> "title")'
    candidate = r'[ label [with brackets] \] &amp; `code` ](<path with spaces> "title")'

    assert repair_markdown_link_label_padding(source, candidate) == (
        r'[label [with brackets] \] &amp; `code`](<path with spaces> "title")'
    )


def test_repair_changes_only_boundary_padding_and_is_idempotent() -> None:
    source = "Before [метка с  внутренними  пробелами](it's.md) after.\n"
    candidate = "Before [ label with  intentional  spaces ](it's.md) after.\n"
    expected = "Before [label with  intentional  spaces](it's.md) after.\n"

    repaired = repair_markdown_link_label_padding(source, candidate)

    assert repaired == expected
    assert repair_markdown_link_label_padding(source, repaired) == expected


def test_repair_leaves_non_inline_and_unsafe_constructs_byte_stable() -> None:
    source = (
        "![ image ](asset.png)\n"
        "[ reference ][id]\n"
        "<https://example.test/a>\n"
        "`[ code ](literal)`\n"
        "```md\n[ fenced ](literal)\n```\n"
        "[ malformed ](destination\n"
        "\n[id]: target.md\n"
    )

    assert repair_markdown_link_label_padding(source, source) == source


def test_repair_keeps_padding_when_source_label_has_padding() -> None:
    source = "See [ намеренно ](a.md).\n"
    candidate = "See [ intentional ](a.md).\n"

    assert repair_markdown_link_label_padding(source, candidate) == candidate


def test_repair_fails_closed_when_inline_link_counts_do_not_align() -> None:
    source = "See [одна](a.md).\n"
    candidate = "See [ one ](a.md) and [ extra ](b.md).\n"

    assert repair_markdown_link_label_padding(source, candidate) == candidate


def test_repair_does_not_collapse_multiline_boundary_whitespace() -> None:
    source = "See [метка](a.md).\n"
    candidate = "See [\n label\n](a.md).\n"

    assert repair_markdown_link_label_padding(source, candidate) == candidate


def test_repair_leaves_a_padded_label_with_an_internal_newline_unchanged() -> None:
    source = "See [метка](a.md).\n"
    candidate = "See [ label\ncontinued ](a.md).\n"

    assert repair_markdown_link_label_padding(source, candidate) == candidate


def test_repair_does_not_hide_an_unrelated_editorial_warning() -> None:
    source = "См. [метку](a.md).\n"
    candidate = "See [ label ](a.md), then use the ldaps schema.\n"

    repaired = repair_markdown_link_label_padding(source, candidate)

    assert repaired == "See [label](a.md), then use the ldaps schema.\n"
    assert check_en_editorial(repaired) == [
        "editorial_ldap_scheme: line 1: "
        "«See [label](a.md), then use the ldaps schema.»"
    ]


def test_repair_pairs_reordered_links_by_destination_and_title() -> None:
    source = '- [ намеренно ](a.md "A")\n- [без](b.md "B")\n'
    candidate = '- [ translated b ](b.md "B")\n- [ intentional a ](a.md "A")\n'

    assert repair_markdown_link_label_padding(source, candidate) == (
        '- [translated b](b.md "B")\n- [ intentional a ](a.md "A")\n'
    )


def test_repair_uses_title_to_disambiguate_a_shared_destination() -> None:
    source = '- [ намеренно ](same.md "A")\n- [без](same.md "B")\n'
    candidate = (
        '- [ translated b ](same.md "B")\n'
        '- [ intentional a ](same.md "A")\n'
    )

    assert repair_markdown_link_label_padding(source, candidate) == (
        '- [translated b](same.md "B")\n'
        '- [ intentional a ](same.md "A")\n'
    )


def test_repair_leaves_ambiguous_duplicate_identity_unchanged() -> None:
    source = "- [первая](same.md)\n- [вторая](same.md)\n"
    candidate = "- [ first ](same.md)\n- [ second ](same.md)\n"

    assert repair_markdown_link_label_padding(source, candidate) == candidate
