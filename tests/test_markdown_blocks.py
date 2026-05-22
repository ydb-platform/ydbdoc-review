from ydbdoc_review.markdown_blocks import (
    dedupe_duplicate_h1_block,
    join_markdown_blocks,
    realign_en_prose_with_ru_blocks,
    repair_block_translation_artifacts,
    split_markdown_blocks,
    translate_preserving_blocks,
)
from ydbdoc_review.translate_postprocess import (
    fix_cli_explain_commands,
    fix_cli_explain_from_ru,
    fix_grant_classifier_use_from_ru,
)


def test_split_preserves_fence():
    md = """Intro text.

```sql
GRANT USE ON `x`;
```

More text.
"""
    blocks = split_markdown_blocks(md)
    kinds = [b.kind for b in blocks]
    assert kinds == ["prose", "fence", "prose"]
    assert "GRANT USE" in blocks[1].text
    assert join_markdown_blocks(blocks) == md


def test_translate_preserving_blocks():
    md = "RU intro\n\n```\ncode\n```\n\nRU tail\n"
    out = translate_preserving_blocks(md, lambda p: p.replace("RU", "EN"))
    assert "EN intro" in out
    assert "EN tail" in out
    assert "```\ncode\n```" in out
    assert "RU" not in out


def test_repair_placeholder_leak_and_duplicate_h1():
    ru = (
        "# Title\n\n"
        "RU intro.\n\n"
        "{% note warning %}\n\nRU warn\n{% endnote %}\n\n"
        "## Section\n\nRU body\n"
    )
    en = (
        "# Title\n\n"
        "EN intro.\n\n"
        "# Title\n\n"
        "EN intro dup.\n\n"
        "⟦YDBDOC_BLOCK_0⟧\n\n"
        "{% note warning %}\n\nRU warn\n{% endnote %}\n\n"
        "## Section\n\nEN body\n"
    )
    fixed = repair_block_translation_artifacts(ru, en)
    assert "YDBDOC_BLOCK" not in fixed
    assert fixed.count("# Title") == 1
    assert "EN intro dup" not in fixed
    assert "{% note warning %}" in fixed
    assert "EN body" in fixed


def test_realign_keeps_liquid_from_ru():
    ru = "# T\n\n{% note %}\n\nx\n{% endnote %}\n"
    en = "# T\n\n⟦YDBDOC_BLOCK_0⟧\n\nEN lead\n"
    out = realign_en_prose_with_ru_blocks(ru, en)
    assert "YDBDOC_BLOCK" not in out
    assert "{% note %}" in out


def test_dedupe_duplicate_h1():
    text = "# A\n\np1\n\n# A\n\np2\n\n⟦YDBDOC_BLOCK_0⟧\n\n## Next\n\nx\n"
    out = dedupe_duplicate_h1_block(text)
    assert out.count("# A") == 1
    assert "## Next" in out


def test_fix_explain_from_ru():
    ru = "Эквивалент: `ydb sql --explain`"
    en = "Use `ydb table query explain`"
    assert "ydb sql --explain" in fix_cli_explain_from_ru(ru, en)


def test_fix_explain_commands_always():
    en = "`ydb table query explain --ast`"
    assert "ydb sql --explain-ast" in fix_cli_explain_commands(en)


def test_fix_grant_use_classifier():
    ru = "GRANT USE ON `my_classifier`"
    en = "GRANT ALL ON `my_classifier`"
    assert "GRANT USE" in fix_grant_classifier_use_from_ru(ru, en)
