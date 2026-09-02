# ruff: noqa: RUF001

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

import ydbdoc_review.parsing.front_matter as front_matter_module
import ydbdoc_review.parsing.markdown_parser as markdown_parser_module
import ydbdoc_review.parsing.yfm_plugins.source_spans as source_spans_module
import ydbdoc_review.translation.one_pass as one_pass_module
from ydbdoc_review.parsing.markdown_parser import (
    ParserSpanRecord,
    _canonical_source_slice,
    parse_markdown,
)
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.one_pass import (
    OnePassTranslationError,
    build_complete_document_validation_context,
    validate_complete_document,
)


def context(source: str, *, anchors=(), toc=None):
    return build_complete_document_validation_context(
        source,
        "ydb/docs/ru/core/page.md",
        extract_segments(parse_markdown(source)),
        anchors,
        toc,
    )


def test_positive_complete_document_with_structured_link_anchor_and_fence():
    source = "# Русский {#якорь}\n\nТекст [ссылка](/ru/core/a.md?q=1#f).\n\n```py\nПривет\n```\n"
    candidate = "# English {#anchor}\n\nText [link](/en/core/a.md?q=1#f).\n\n```py\nПривет\n```\n"
    validate_complete_document(candidate, context(source, anchors=(("якорь", "anchor"),)))


@pytest.mark.parametrize(
    ("candidate", "error"),
    [
        ("English ⟦U1⟧\n", "unrestored_protect_token"),
        ("- English\n", "container_structure_parity"),
        ("Русский\n", "residual_cyrillic_prose"),
    ],
)
def test_fail_closed_basic_corruptions(candidate, error):
    with pytest.raises(OnePassTranslationError, match=error):
        validate_complete_document(candidate, context("Текст\n"))


def test_non_prose_mutation_rejected_but_cyrillic_fence_allowed():
    source = "Текст\n\n```py\nПривет\n```\n"
    validate_complete_document("Text\n\n```py\nПривет\n```\n", context(source))
    with pytest.raises(OnePassTranslationError, match="fence_config_parity"):
        validate_complete_document("Text\n\n```js\nПривет\n```\n", context(source))


def test_href_and_ascii_anchor_are_exact():
    source = "# Русский {#same}\n\n[ссылка](a.md?q=1#f)\n"
    valid = "# English {#same}\n\n[link](a.md?q=1#f)\n"
    validate_complete_document(valid, context(source))
    with pytest.raises(OnePassTranslationError, match="protected_atom_parity"):
        validate_complete_document(valid.replace("q=1", "q=2"), context(source))
    with pytest.raises(OnePassTranslationError, match="explicit_anchor_parity"):
        validate_complete_document(valid.replace("#same}", "#other}"), context(source))


def test_residual_projection_is_context_invariant():
    source = "Text\n\n```\nx\n```\n"
    validation_context = context(source)
    broken = replace(validation_context, residual_cyrillic_allowed_ranges=())
    with pytest.raises(OnePassTranslationError, match="validation_context_invalid"):
        validate_complete_document(source, broken)


def test_note_and_cut_titles_may_change_length_but_syntax_may_not():
    source = "{% note info \"Заголовок\" %}\nТекст\n{% endnote %}\n\n{% cut \"Раздел\" %}\nТекст\n{% endcut %}\n"
    candidate = "{% note info \"A much longer title\" %}\nText\n{% endnote %}\n\n{% cut \"Section\" %}\nText\n{% endcut %}\n"
    validate_complete_document(candidate, context(source))
    with pytest.raises(OnePassTranslationError, match="container_structure_parity"):
        validate_complete_document(candidate.replace("note info", "note warning"), context(source))


def test_front_matter_selected_values_change_but_unselected_bytes_do_not():
    source = "---\ntitle: Привет\ndescription: \"Описание\"\nx: 1 # keep\n---\n\nТекст\n"
    candidate = "---\ntitle: English title\ndescription: \"Description\"\nx: 1 # keep\n---\n\nText\n"
    validate_complete_document(candidate, context(source))
    with pytest.raises(OnePassTranslationError, match="fence_config_parity"):
        validate_complete_document(candidate.replace("x: 1", "x: 2"), context(source))


def test_toc_reachability_is_conditional():
    source = "[ссылка](a.md#f)\n"
    candidate = "[link](a.md#f)\n"
    validate_complete_document(candidate, context(source, toc=None))
    with pytest.raises(OnePassTranslationError, match="unreachable_en_internal_link"):
        validate_complete_document(candidate, context(source, toc=frozenset()))


def test_percent_encoded_cyrillic_fragment_maps_with_accepted_anchor():
    from urllib.parse import quote

    source_anchor = "точный-якорь"
    encoded = quote(source_anchor, safe="")
    source = f"# Русский {{#{source_anchor}}}\n\n[сюда](#{encoded})\n"
    candidate = "# English {#heading}\n\n[here](#heading)\n"
    validate_complete_document(
        candidate,
        context(source, anchors=((source_anchor, "heading"),)),
    )


def test_tab_list_marker_mutation_is_rejected():
    source = "{% list tabs %}\n\n- Русский\n\n  Текст.\n\n{% endlist %}\n"
    candidate = "{% list tabs %}\n\n+ English\n\n  Text.\n\n{% endlist %}\n"
    with pytest.raises(OnePassTranslationError, match="fence_config_parity"):
        validate_complete_document(candidate, context(source))


@pytest.mark.parametrize("marker", ["+", "*"])
def test_tab_physical_marker_mutation_is_rejected(marker):
    source = "{% list tabs %}\n- Русский\n\n  Текст\n{% endlist %}\n"
    candidate = f"{{% list tabs %}}\n{marker} English\n\n  Text\n{{% endlist %}}\n"
    with pytest.raises(OnePassTranslationError, match="fence_config_parity"):
        validate_complete_document(candidate, context(source))


def test_atom_loss_reorder_and_content_are_rejected():
    source = "Текст `one` and `two`.\n"
    validation_context = context(source)
    for candidate in (
        "Text `one`.\n",
        "Text `two` and `one`.\n",
        "Text `one` and `changed`.\n",
    ):
        with pytest.raises(OnePassTranslationError, match="protected_atom_parity"):
            validate_complete_document(candidate, validation_context)


@pytest.mark.parametrize(
    ("kind", "role"),
    [
        ("paragraph_open", "note_title"),
        ("yfm_note_open", "cut_title"),
        ("yfm_cut_open", "tab_title"),
        ("front_matter", "front_matter:other"),
    ],
)
def test_translatable_role_matrix_rejects_foreign_roles(kind, role):
    record = ParserSpanRecord(kind, 0, 1, kind, ((0, 1, role),))
    with pytest.raises(ValueError, match="source_map_invalid_translatable_span"):
        _canonical_source_slice("x", record)


@pytest.mark.parametrize(
    "function",
    [
        markdown_parser_module._canonical_source_slice,
        markdown_parser_module._record_from_token_map,
        markdown_parser_module._build_parser_source_map,
        front_matter_module.parse_front_matter_with_spans,
        front_matter_module.apply_front_matter_updates,
        one_pass_module._canonical_atom_payload,
        one_pass_module._map_expected_destination,
        one_pass_module.build_complete_document_validation_context,
        one_pass_module.validate_complete_document,
        source_spans_module.utf8_source_span,
    ],
)
def test_parser_owned_validation_functions_have_no_forbidden_search(function):
    tree = ast.parse(inspect.getsource(function))
    forbidden = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"find", "rfind", "index", "replace"}
    }
    assert forbidden == set()


@pytest.mark.parametrize(
    ("source", "candidate", "error"),
    [
        ("# A\n\nТекст\n", "## English\n\nText\n", "container_structure_parity"),
        ("> Цитата\n", "- Quote\n", "container_structure_parity"),
        ("- Один\n- Два\n", "1. One\n2. Two\n", "container_structure_parity"),
        ("Текст `a` и `b`.\n", "Text `a` and `a`.\n", "protected_atom_parity"),
        ("[t](a.md?x=1#f)\n", "[t](a.md?x=2#f)\n", "protected_atom_parity"),
        ("[t](a.md?x=1#f)\n", "[t](a.md?x=1#g)\n", "protected_atom_parity"),
        ("[t](a.md \"Титул\")\n", "[t](a.md \"Title\")\n", "protected_atom_parity"),
        ("# Русский {#якорь}\n", "# English {#other}\n", "explicit_anchor_parity"),
        ("```bash\nкод\n```\n", "````bash\nкод\n````\n", "fence_config_parity"),
        ("```py\nx\n```\n", "```js\nx\n```\n", "fence_config_parity"),
        (
            "{% include [a](./a.md) %}\n",
            "{% include [a](./b.md) %}\n",
            "fence_config_parity",
        ),
        (
            '{% if audience == "x" %}\nТекст\n{% endif %}\n',
            '{% if audience == "y" %}\nText\n{% endif %}\n',
            "container_structure_parity",
        ),
        (
            "{% if a %}\nA\n{% else %}\nB\n{% endif %}\n",
            "{% if a %}\nA\n{% endif %}\n",
            "container_structure_parity",
        ),
        (
            "---\ntitle: Привет\ndescription: D\n---\n\nТекст\n",
            "---\ndescription: D\ntitle: Hello\n---\n\nText\n",
            "fence_config_parity",
        ),
        (
            '{% note info "T" %}\nТекст\n{% endnote %}\n',
            '{% note tip "T" %}\nText\n{% endnote %}\n',
            "container_structure_parity",
        ),
        (
            '{% note info "T" %}\nТекст\n{% endnote %}\n',
            "{% note info 'T' %}\nText\n{% endnote %}\n",
            "container_structure_parity",
        ),
        (
            '{% cut "T" %}\nТекст\n{% endcut %}\n',
            '{% cut "T"  %}\nText\n{% endcut %}\n',
            "fence_config_parity",
        ),
        (
            "{% list tabs %}\n- A\n\n  Текст\n{% endlist %}\n",
            "{% list tabs %}\n- A\n\n  Text\n{% endtabs %}\n",
            "container_structure_parity",
        ),
    ],
)
def test_isolated_structure_atom_link_fence_yfm_front_matter_mutations(
    source: str, candidate: str, error: str
) -> None:
    with pytest.raises(OnePassTranslationError, match=error):
        validate_complete_document(
            candidate, context(source, anchors=(("якорь", "anchor"),))
        )


def test_note_empty_and_absent_title_distinctions() -> None:
    titled = '{% note info "Заголовок" %}\nТекст\n{% endnote %}\n'
    empty = '{% note info "" %}\nТекст\n{% endnote %}\n'
    absent = "{% note info %}\nТекст\n{% endnote %}\n"
    validate_complete_document(
        '{% note info "English title" %}\nText\n{% endnote %}\n', context(titled)
    )
    validate_complete_document('{% note info "" %}\nText\n{% endnote %}\n', context(empty))
    validate_complete_document("{% note info %}\nText\n{% endnote %}\n", context(absent))
    with pytest.raises(OnePassTranslationError):
        validate_complete_document(
            '{% note info "" %}\nText\n{% endnote %}\n', context(absent)
        )


def test_cut_empty_title_zero_width_span_round_trip() -> None:
    source = '{% cut "" %}\nТекст\n{% endcut %}\n'
    validate_complete_document('{% cut "" %}\nText\n{% endcut %}\n', context(source))
    with pytest.raises(OnePassTranslationError, match="container_structure_parity"):
        validate_complete_document("{% cut %}\nText\n{% endcut %}\n", context(source))


def test_source_spans_module_forbids_re_and_text_search_apis() -> None:
    source = Path(source_spans_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "re" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"find", "rfind", "index", "replace"}
        ):
            raise AssertionError(node.func.attr)
    assert "_FENCE" not in source


def test_missing_yfm_source_span_fails_closed_without_map_fallback() -> None:
    from markdown_it.token import Token

    from ydbdoc_review.parsing.markdown_parser import _record_from_token_map

    token = Token("yfm_note_open", "", 0)
    token.map = [0, 1]
    token.meta = {}
    with pytest.raises(ValueError, match="source_map_incomplete:yfm_note_open"):
        _record_from_token_map(
            token,
            (0, 10, 20),
            kind="yfm_note_open",
            descriptor="yfm_note_open",
        )


def test_zero_width_physical_yfm_marker_fails_closed() -> None:
    from markdown_it.token import Token

    from ydbdoc_review.parsing.markdown_parser import (
        _PARSE_SOURCE,
        SourceSpan,
        _record_from_token_map,
    )

    token = Token("yfm_include", "", 0)
    token.meta = {
        "source_span": SourceSpan(
            byte_start=4, byte_end=4, line=1, column=5
        ).model_dump()
    }
    token_var = _PARSE_SOURCE.set("abcdefghij\n")
    try:
        with pytest.raises(ValueError, match="source_map_incomplete:yfm_include"):
            _record_from_token_map(
                token,
                (0, 11, 11),
                kind="yfm_include",
                descriptor="yfm_include",
            )
    finally:
        _PARSE_SOURCE.reset(token_var)


def test_physical_yfm_span_outside_owned_line_fails_closed() -> None:
    from markdown_it.token import Token

    from ydbdoc_review.parsing.markdown_parser import (
        _PARSE_SOURCE,
        _record_from_token_map,
    )

    source = "{% note info %}\nText\n{% endnote %}\n"
    token = Token("yfm_note_open", "", 0)
    # Claim line 1 but point at bytes on line 2.
    token.meta = {
        "source_span": {
            "byte_start": len("{% note info %}\n"),
            "byte_end": len("{% note info %}\nText"),
            "line": 1,
            "column": 1,
        }
    }
    token_var = _PARSE_SOURCE.set(source)
    try:
        with pytest.raises(ValueError, match="source_map_incomplete:yfm_note_open"):
            _record_from_token_map(
                token,
                (0, len("{% note info %}\n"), len(source.encode("utf-8"))),
                kind="yfm_note_open",
                descriptor="yfm_note_open",
            )
    finally:
        _PARSE_SOURCE.reset(token_var)


def test_non_zero_virtual_close_fails_closed() -> None:
    from markdown_it.token import Token

    from ydbdoc_review.parsing.markdown_parser import _PARSE_SOURCE, _record_from_token_map

    source = "line\n"
    token = Token("yfm_tab_close", "", 0)
    token.meta = {
        "source_span": {
            "byte_start": 0,
            "byte_end": 4,
            "line": 1,
            "column": 1,
        }
    }
    token_var = _PARSE_SOURCE.set(source)
    try:
        with pytest.raises(ValueError, match="source_map_incomplete:yfm_tab_close"):
            _record_from_token_map(
                token,
                (0, 5, 5),
                kind="yfm_tab_close",
                descriptor="yfm_tab_close",
            )
    finally:
        _PARSE_SOURCE.reset(token_var)


def test_front_matter_description_and_comment_bytes_are_protected() -> None:
    source = (
        "---\n"
        "title: Привет\n"
        'description: "Описание" # keep\n'
        "x: 1\n"
        "---\n\n"
        "Текст\n"
    )
    valid = (
        "---\n"
        "title: Hello\n"
        'description: "Description" # keep\n'
        "x: 1\n"
        "---\n\n"
        "Text\n"
    )
    validate_complete_document(valid, context(source))
    with pytest.raises(OnePassTranslationError, match="fence_config_parity"):
        validate_complete_document(valid.replace("# keep", "# changed"), context(source))



def test_absolute_ru_same_document_fragment_is_localized():
    from ydbdoc_review.translation.one_pass import _map_expected_destination

    mapped = _map_expected_destination(
        "/ru/core/page.md#якорь",
        "ydb/docs/ru/core/page.md",
        (("якорь", "anchor"),),
    )
    assert mapped == "/en/core/page.md#anchor"


def test_parser_failure_is_fail_closed():
    source = "---\ntitle: Привет\n---\n\nТекст\n"
    with pytest.raises(OnePassTranslationError, match="candidate_parse_failed"):
        validate_complete_document(
            "---\nx: &a Hello\ntitle: *a\n---\n\nText\n",
            context(source),
        )


def test_container_reorder_and_nesting_are_rejected():
    source = "- Один\n\n  - Вложенный\n"
    with pytest.raises(OnePassTranslationError, match="container_structure_parity"):
        validate_complete_document("- Nested\n\n- One\n", context(source))
    with pytest.raises(OnePassTranslationError, match="container_structure_parity"):
        validate_complete_document(
            "- One\n\n  - Nested\n\n    - Deeper\n",
            context(source),
        )


def test_standalone_atom_duplication_is_rejected():
    source = "Текст `only`.\n"
    with pytest.raises(OnePassTranslationError, match="protected_atom_parity"):
        validate_complete_document("Text `only` and `only`.\n", context(source))


def test_link_path_and_order_mutations_are_rejected():
    source = "[a](first.md) [b](second.md)\n"
    with pytest.raises(OnePassTranslationError, match="protected_atom_parity"):
        validate_complete_document("[a](other.md) [b](second.md)\n", context(source))
    with pytest.raises(OnePassTranslationError, match="protected_atom_parity"):
        validate_complete_document("[b](second.md) [a](first.md)\n", context(source))


def test_fence_body_and_closing_marker_mutations_are_rejected():
    source = "```py\nкод\n```\n"
    with pytest.raises(OnePassTranslationError, match="fence_config_parity"):
        validate_complete_document("```py\nbody\n```\n", context(source))
    with pytest.raises(OnePassTranslationError, match="fence_config_parity"):
        validate_complete_document("```py\nкод\n````\n", context(source))


@pytest.mark.parametrize(
    "broken",
    [
        "---\nTitle: Hello # keep\nx: 1\n---\n\nText\n",
        "---\ntitle: Hello # changed\nx: 1\n---\n\nText\n",
        "---\ntitle: Hello # keep\nx: 2\n---\n\nText\n",
        "---\ndescription: D\ntitle: Hello # keep\nx: 1\n---\n\nText\n",
    ],
)
def test_front_matter_fail_closed_protected_matrix(broken: str) -> None:
    source = "---\ntitle: Привет # keep\nx: 1\n---\n\nТекст\n"
    with pytest.raises(OnePassTranslationError):
        validate_complete_document(broken, context(source))
