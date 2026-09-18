"""Historical YFM inputs through production grammar, protection and restoration.

Legacy AST class shape is not a product invariant; syntax, prose and atoms are.
"""

import json
from pathlib import Path

import pytest

from tests.roundtrip import roundtrip
from ydbdoc_review.document import protect
from ydbdoc_review.parsing.markdown_parser import create_parser

CASES = json.loads((Path(__file__).parents[1] / "fixtures/yfm_survivors.json").read_text())
assert CASES


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_original_yfm_source_spans_and_roundtrip(case):
    text = case["source"]
    tokens = create_parser(source_locations=True).parse(text)
    assert tokens or not text.strip()
    assert roundtrip(text) == text
    p = protect(text)
    assert all(p.source[a.start : a.end] == a.raw for a in p.atoms)
    assert len({a.marker for a in p.atoms}) == len(p.atoms)


@pytest.mark.parametrize(
    "source,types",
    [
        (
            '{% cut "Title" %}\n\nBody\n\n{% endcut %}\n',
            ["yfm_cut_open", "paragraph_open", "yfm_cut_close"],
        ),
        (
            "{% note info %}\n\nBody\n\n{% endnote %}\n",
            ["yfm_note_open", "paragraph_open", "yfm_note_close"],
        ),
        ("{% include [name](../file.md) %}\n", ["yfm_include"]),
    ],
)
def test_containers_are_grammar_tokens_and_protected(source, types):
    tokens = create_parser(source_locations=True).parse(source)
    actual = [t.type for t in tokens]
    assert all(t in actual for t in types)
    p = protect(source)
    if "Body" in source:
        assert "Body" in p.text
    assert "{%" not in p.text
    assert roundtrip(source) == source


def test_variable_and_sized_image_preserve_targets_and_expose_alt():
    text = "Hello {{ name }} ![Caption](image.png =100x200)\n"
    p = protect(text)
    assert "Hello" in p.text and "Caption" in p.text
    assert "{{ name }}" not in p.text and "image.png" not in p.text
    assert roundtrip(text) == text
