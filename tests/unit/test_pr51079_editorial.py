"""Regression coverage for the narrow PR #51079 editorial checks."""

from __future__ import annotations

from textwrap import dedent

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.parsing.markdown_parser import (
    parse_markdown,
    parse_markdown_located,
)
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.qa import compose_file_verdict
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.validation.editorial import check_en_editorial
from ydbdoc_review.validation.heuristics import (
    _classify_heuristic,
    run_file_heuristics_classified,
)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (
            "See [ section `use_tls` ](auth.md#tls).",
            "editorial_link_label_space:",
        ),
        (
            "Connect [ using the `StartTls` request ](auth.md#starttls).",
            "editorial_link_label_space:",
        ),
        ("Use the `ldaps` schema to connect.", "editorial_ldap_scheme:"),
        ("Use the ldaps schema to connect.", "editorial_ldap_scheme:"),
    ],
)
def test_incident_editorial_forms_are_non_green(text: str, code: str) -> None:
    messages = check_en_editorial(text)

    assert any(message.startswith(code) for message in messages)
    assert all(_classify_heuristic(message) == "warnings" for message in messages)


@pytest.mark.parametrize(
    "text",
    [
        "Use the `ldaps` scheme.",
        "The LDAP schema describes directory attributes.",
        "See [the `use_tls` section](auth.md#tls).",
        "Connect [using the `StartTls` request](auth.md#starttls).",
        "```text\n[ section use_tls ](auth.md)\nldaps schema\n```\n",
        "See [example](https://example.test/ldaps-schema).",
    ],
)
def test_editorial_controls_are_clean(text: str) -> None:
    assert check_en_editorial(text) == []


def test_editorial_walks_yfm_tables_and_nested_containers() -> None:
    text = dedent(
        """
        {% note info %}

        | Setting | Description |
        | --- | --- |
        | TLS | Use `LDAPS` SCHEMA for encrypted connections. |

        {% if audience == "admin" %}

        > See [ padded `use_tls` ](auth.md#tls).

        {% endif %}

        {% endnote %}
        """
    ).lstrip()

    messages = check_en_editorial(text)

    assert sum(message.startswith("editorial_ldap_scheme:") for message in messages) == 1
    assert sum(
        message.startswith("editorial_link_label_space:") for message in messages
    ) == 1


def test_link_space_locations_use_original_lines() -> None:
    text = dedent(
        """
        Intro with `code` that must not alter offsets.

        See [ section `use_tls` ](auth.md#tls).
        Use the `ldaps` schema to connect.
        """
    ).lstrip()

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 3: "
        "«See [ section `use_tls` ](auth.md#tls).»",
        "editorial_ldap_scheme: line 4: «Use the `ldaps` schema to connect.»",
    ]


def test_link_location_is_not_shifted_by_reference_style_link() -> None:
    text = "Normal [ok][ref].\nSee [ padded](b).\n\n[ref]: a\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «See [ padded](b).»"
    ]


def test_link_location_is_not_shifted_by_autolink() -> None:
    text = "Normal <https://example.test>.\nSee [ padded](b).\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «See [ padded](b).»"
    ]


def test_link_location_accepts_apostrophe_in_bare_destination() -> None:
    text = "Intro.\nSee [ padded](it's.md).\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «See [ padded](it's.md).»"
    ]


def test_multiline_link_location_uses_the_exact_ast_link_span() -> None:
    text = "Normal [ok](a).\nSee [ padded\nlabel](b).\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «See [ padded»"
    ]


def test_multiline_link_trailing_softbreak_ignores_skipped_indentation() -> None:
    text = "Normal [ok](a).\nSee [label\n ](b).\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «See [label»"
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '[x](proto.md "ldaps schema")\nUse **LDAPS** SCHEMA now.\n',
            "editorial_ldap_scheme: line 2: «Use **LDAPS** SCHEMA now.»",
        ),
        (
            "Intro line.\nUse **LDAPS** SCHEMA now.\n",
            "editorial_ldap_scheme: line 2: «Use **LDAPS** SCHEMA now.»",
        ),
        (
            "Intro line.\nUse [LDAPS](proto.md) schema now.\n",
            "editorial_ldap_scheme: line 2: «Use [LDAPS](proto.md) schema now.»",
        ),
    ],
)
def test_ldap_location_uses_the_exact_visible_ast_match(
    text: str, expected: str
) -> None:
    assert check_en_editorial(text) == [expected]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "<https://example.test/ldaps>\nUse ldaps schema now.\n",
            "editorial_ldap_scheme: line 2: «Use ldaps schema now.»",
        ),
        (
            "Intro.\nUse lda&#112;s schema now.\n",
            "editorial_ldap_scheme: line 2: «Use lda&#112;s schema now.»",
        ),
    ],
)
def test_ldap_location_uses_parser_visible_stream(
    text: str, expected: str
) -> None:
    assert check_en_editorial(text) == [expected]


def test_editorial_ignores_front_matter_comments_and_code_examples() -> None:
    text = dedent(
        """
        ---
        title: ldaps schema
        link: "[ padded ](auth.md)"
        ---

        <!-- ldaps schema and [ padded ](auth.md) -->
        Visible prose uses the `ldaps` scheme.

            ldaps schema
            [ padded ](auth.md)

        ```text
        ldaps schema
        [ padded ](auth.md)
        ```
        """
    ).lstrip()

    assert check_en_editorial(text) == []


def test_editorial_warning_prevents_green_even_with_critic_ok() -> None:
    target = "See [ padded](auth.md#tls)."
    heuristics = run_file_heuristics_classified(
        target,
        target,
        normalized_source_text=target,
        source_lang="en",
        target_lang="en",
    )
    verdict = compose_file_verdict(
        critic_verdict="ok",
        alignment_error=None,
        heuristics=heuristics,
        manual_actions=False,
    )
    assert verdict == "warnings"
    assert heuristics.blocking == []
    assert any(
        message.startswith("editorial_link_label_space:")
        for message in heuristics.warnings
    )

    pair = DocPair(
        ru_path="ydb/docs/ru/auth.md",
        en_path="ydb/docs/en/auth.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    file_result = FileTranslationResult(
        file_path=pair.en_path,
        final_text=target,
        segments_count=1,
        verdict=verdict,
        critic_initial=CriticResponse(verdict="ok", issues=[]),
        critic_unresolved=CriticResponse(verdict="ok", issues=[]),
        heuristic_blocking=[],
        heuristic_warnings=list(heuristics.warnings),
        heuristic_info=[],
        prompt_version="v1",
    )
    report = build_full_report(
        PRTranslationResult(
            pair_results=[PairRunResult(plan=plan, file_result=file_result)]
        ),
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=load_config(
            env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}
        ),
    )

    assert "🟡" in report
    assert "можно мержить" not in report
    assert "Уберите пробелы по краям текста ссылки" in report


def test_non_english_target_is_out_of_scope() -> None:
    assert check_en_editorial("Use ldaps schema and [ padded](auth.md).", target_lang="ru") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Intro. Use `ignored\nldaps schema` now.\n",
            "editorial_ldap_scheme: line 2: «ldaps schema` now.»",
        ),
        (
            "Intro.<!--\nldaps schema\n-->\nUse ldaps schema now.\n",
            "editorial_ldap_scheme: line 4: «Use ldaps schema now.»",
        ),
        (
            '[x](proto.md\n "ldaps schema")\nUse ldaps schema now.\n',
            "editorial_ldap_scheme: line 3: «Use ldaps schema now.»",
        ),
    ],
)
def test_ldap_location_survives_hidden_or_normalized_multiline_inline_syntax(
    text: str, expected: str
) -> None:
    assert check_en_editorial(text) == [expected]


def test_escaped_image_marker_leaves_a_real_link_with_an_exact_location() -> None:
    text = "Intro.\n\\![ padded](a).\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «\\![ padded](a).»"
    ]


def test_inline_code_payload_supplies_trailing_visible_label_space() -> None:
    text = "Intro.\nSee [`use_tls `](a).\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «See [`use_tls `](a).»"
    ]


def test_yfm_table_findings_use_the_synthesized_cell_source_line() -> None:
    text = dedent(
        """
        #|
        || Setting | Description ||
        || TLS | Use ldaps schema and see [ padded](auth.md). ||
        |#
        """
    ).lstrip()

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 3: "
        "«|| TLS | Use ldaps schema and see [ padded](auth.md). ||»",
        "editorial_ldap_scheme: line 3: "
        "«|| TLS | Use ldaps schema and see [ padded](auth.md). ||»",
    ]


def test_located_parse_preserves_link_variable_coordinates_and_ast() -> None:
    text = "See [ok](path/{{ var }}/x) then [ padded](a).\n"

    located = parse_markdown_located(text)

    assert located.document == parse_markdown(text)
    assert len(located.inline_segments) == 1
    assert [link.label.text for link in located.inline_segments[0].links] == [
        "ok",
        " padded",
    ]
    padded = located.inline_segments[0].links[1].label
    assert padded.spans[0].start == text.index(" padded")
    assert padded.spans[0].end == text.index(" padded") + 1


@pytest.mark.parametrize(
    "text",
    [
        "Use lda&#112;s **schema** and <https://example.test/ldaps>.\n",
        "Intro. Use `ignored\nldaps schema` now.\n",
        "\\![ padded](a) and ![ image](img.png).\n",
        "![image](img.png =10x20) then text.\n",
        "Normal [ok][ref].\nSee [ padded\nlabel](b).\n\n[ref]: a\n",
        "#|\n|| A | B ||\n|| x | Use ldaps schema. ||\n|#\n",
        "# Use **ldaps** schema {#connection}\n",
        "> Intro.<!--\n> hidden\n> -->\n> Use ldaps schema.\n",
        "[*connection]: Use ldaps schema.\n",
    ],
)
def test_located_parse_is_ast_equivalent_for_editorial_constructs(text: str) -> None:
    assert parse_markdown_located(text).document == parse_markdown(text)


def test_discarded_strike_subtree_excludes_its_links_from_the_sidecar() -> None:
    text = "~~[x](a)~~\nUse ldaps schema.\n"

    assert parse_markdown_located(text).document == parse_markdown(text)
    assert check_en_editorial(text) == [
        "editorial_ldap_scheme: line 2: «Use ldaps schema.»"
    ]


def test_nested_autolink_follows_outer_first_ast_link_order() -> None:
    text = "[ outer <https://example.test>](dest)\n"

    located = parse_markdown_located(text)

    assert located.document == parse_markdown(text)
    assert [link.node.href for link in located.inline_segments[0].links] == [
        "dest",
        "https://example.test",
    ]
    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 1: "
        "«[ outer <https://example.test>](dest)»"
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Intro.\rUse ldaps schema.\r",
            "editorial_ldap_scheme: line 2: «Use ldaps schema.»",
        ),
        (
            "Intro.\rSee [ padded](a).\r",
            "editorial_link_label_space: line 2: «See [ padded](a).»",
        ),
    ],
)
def test_editorial_locations_honor_lone_cr_newlines(
    text: str, expected: str
) -> None:
    assert check_en_editorial(text) == [expected]


def test_located_block_state_preserves_blank_line_indented_code() -> None:
    text = "    a\n\n    b\n"

    assert parse_markdown_located(text).document == parse_markdown(text)


def test_multiline_link_label_preserves_nbsp_boundary_location() -> None:
    text = "* See [padded\n\u00a0](x)\n"

    assert parse_markdown_located(text).document == parse_markdown(text)
    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 2: «\u00a0](x)»"
    ]


def test_crlf_trailing_label_softbreak_uses_the_originating_line() -> None:
    text = "See [label\r\n](x)\r\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 1: «See [label»"
    ]


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_trailing_label_softbreak_has_equivalent_newline_semantics(
    newline: str,
) -> None:
    text = f"See [label{newline}](x){newline}"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 1: «See [label»"
    ]


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_inline_code_trailing_normalized_newline_is_atomic(newline: str) -> None:
    text = f"See [`label{newline}`](x){newline}"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 1: «See [`label»"
    ]


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_leading_label_softbreak_uses_visible_newline_origin(newline: str) -> None:
    text = f"See [{newline} label](x){newline}"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 1: «See [»"
    ]


def test_softbreak_projection_excludes_all_skipped_next_line_indentation() -> None:
    text = "See [\n   label](x)\n"

    assert check_en_editorial(text) == [
        "editorial_link_label_space: line 1: «See [»"
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Intro.\u2028Use ldaps schema.\n",
            "editorial_ldap_scheme: line 1: «Intro.\u2028Use ldaps schema.»",
        ),
        (
            "Intro.\fSee [ padded](x).\n",
            "editorial_link_label_space: line 1: «Intro.\fSee [ padded](x).»",
        ),
        (
            "Intro.\vUse ldaps schema.\n",
            "editorial_ldap_scheme: line 1: «Intro.\vUse ldaps schema.»",
        ),
        (
            "Intro.\x85Use ldaps schema.\n",
            "editorial_ldap_scheme: line 1: «Intro.\x85Use ldaps schema.»",
        ),
        (
            "Intro.\u2029Use ldaps schema.\n",
            "editorial_ldap_scheme: line 1: «Intro.\u2029Use ldaps schema.»",
        ),
    ],
)
def test_editorial_context_uses_only_commonmark_line_boundaries(
    text: str, expected: str
) -> None:
    assert check_en_editorial(text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "[{{ ydb-short-name }} CLI](cli.md)\n",
        "[cluster {{ ydb-short-name }}](topology.md)\n",
        "[{{ ydb-short-name }} namespace](namespace.md)\n",
        "[cluster {{ ydb-short-name }} namespace](namespace.md)\n",
        "[YDB CLI](cli.md)\n",
        "[**{{ ydb-short-name }} CLI**](cli.md)\n",
        "[**cluster {{ ydb-short-name }}**](topology.md)\n",
    ],
)
def test_link_label_variable_boundaries_are_not_padding(text: str) -> None:
    assert parse_markdown_located(text).document == parse_markdown(text)
    assert check_en_editorial(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "[ {{ ydb-short-name }} CLI](cli.md)\n",
        "[{{ ydb-short-name }} CLI ](cli.md)\n",
        "[ cluster {{ ydb-short-name }}](topology.md)\n",
        "[cluster {{ ydb-short-name }} ](topology.md)\n",
        "[ **{{ ydb-short-name }} CLI**](cli.md)\n",
        "[**cluster {{ ydb-short-name }}** ](topology.md)\n",
    ],
)
def test_link_label_whitespace_outside_variable_boundaries_is_padding(
    text: str,
) -> None:
    assert parse_markdown_located(text).document == parse_markdown(text)
    assert check_en_editorial(text) == [
        f"editorial_link_label_space: line 1: «{text.rstrip()}»"
    ]
