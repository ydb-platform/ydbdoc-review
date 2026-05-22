from ydbdoc_review.markdown_sections import split_markdown_sections
from ydbdoc_review.ru_en_sync import (
    duplicate_h2_sections,
    en_sections_by_heading,
    merge_section_h3_from_ru,
    rebuild_en_document_from_ru,
    section_missing_h3,
    split_h3_blocks,
)
from ydbdoc_review.translate_postprocess import fix_cli_explain_commands


def test_section_missing_h3_detects_gap():
    ru = "## A\n\n### One\n\nru one\n\n### Two\n\nru two\n"
    en = "## A\n\n### One\n\nen one\n"
    assert section_missing_h3(ru, en)


def test_merge_section_adds_missing_h3():
    ru = "## A\n\n### One\n\nru one\n\n### Two\n\nru two\n"
    en = "## A\n\n### One\n\nen one\n"
    calls: list[str] = []

    def fake_translate(block: str) -> str:
        calls.append(block)
        if "ru two" in block:
            return "### Two\n\nen two translated"
        return block.replace("ru", "en")

    out = merge_section_h3_from_ru(ru, en, fake_translate)
    assert "en two translated" in out
    assert "en one" in out
    assert any("ru two" in c for c in calls)


def test_split_h3_blocks_keys():
    parts = split_h3_blocks("## X\n\nlead\n\n### Foo\n\nbody\n")
    keys = [k for k, _ in parts]
    assert "" in keys
    assert "foo" in keys


def test_duplicate_h2_sections_detected():
    en = "## A\n\nx\n\n## A\n\ny\n"
    assert duplicate_h2_sections(en)


def test_rebuild_removes_duplicate_h2_and_orders():
    ru = (
        "## Alpha\n\n### Sub\n\nru sub\n\n"
        "## Beta\n\nru beta\n\n"
        "## Gamma\n\nru gamma\n"
    )
    en = (
        "## Alpha\n\n### Sub\n\nen sub\n\n"
        "## Beta\n\nen beta stale\n\n"
        "## Beta\n\nen beta duplicate longer\n\n"
        "## Orphan\n\nshould drop\n"
    )
    translated: list[str] = []

    def fake_translate(block: str) -> str:
        translated.append(block)
        if "ru gamma" in block:
            return "## Gamma\n\nen gamma new"
        return block.replace("ru", "en")

    out = rebuild_en_document_from_ru(
        None,
        ru_path="ydb/docs/ru/x.md",
        ru_full=ru,
        en_text=en,
        translate_block=fake_translate,
    )
    assert out.count("## Beta") == 1
    assert "en beta duplicate longer" in out
    assert "## Orphan" not in out
    assert "en gamma new" in out
    assert out.index("## Beta") < out.index("## Gamma")
    assert len(en_sections_by_heading(split_markdown_sections(out))) == 3


def test_fix_cli_explain_without_ru_source():
    en = "| EXPLAIN | `ydb table query explain` |"
    fixed = fix_cli_explain_commands(en)
    assert "ydb sql --explain" in fixed
    assert "table query" not in fixed
