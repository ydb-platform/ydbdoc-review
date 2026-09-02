# ruff: noqa: RUF001

from __future__ import annotations

import ast
import inspect
from dataclasses import replace

import pytest

import ydbdoc_review.parsing.front_matter as front_matter_module
import ydbdoc_review.parsing.markdown_parser as markdown_parser_module
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
