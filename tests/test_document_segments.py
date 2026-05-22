from ydbdoc_review.document_segments import parse_document_units


FORMATS_CSV_SNIPPET = """### Формат csv {#csv}

Данный формат основан на формате [CSV](https://ru.wikipedia.org/wiki/CSV).

{% note info %}

Формат `csv` доступен только для чтения.

{% endnote %}

Пример данных:

```text
1997,Man_1,Model_1,3000.00
```

|#|Year|
|-|----|
|1|1997|
"""


def test_parse_csv_subsection_units():
    units = parse_document_units(FORMATS_CSV_SNIPPET, doc_label="formats.md")
    kinds = [u.kind for u in units]
    assert "prose" in kinds
    assert "diplodoc" in kinds
    assert "fence" in kinds
    assert "table" in kinds
    assert kinds.count("fence") == 1
    assert kinds.count("diplodoc") == 1
    assert kinds.count("table") == 1
    # ### heading stays in prose, not a separate giant ## blob
    prose_with_h3 = [u for u in units if u.kind == "prose" and "###" in u.text]
    assert prose_with_h3


def test_h2_splits_formats_style_doc():
    text = """# Title

Intro paragraph.

## Section A {#a}

| A | B |
|---|---|
| 1 | 2 |

### Sub {#sub}

Body text.
"""
    units = parse_document_units(text, doc_label="t.md")
    labels = [u.label for u in units]
    assert any("/h2-1/" in lb for lb in labels)
    assert any("/h3-1/" in lb for lb in labels)
    assert sum(1 for u in units if u.kind == "table") == 1
