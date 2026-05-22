from ydbdoc_review.markdown_blocks import (
    join_markdown_blocks,
    mask_non_prose_for_translate,
    split_markdown_blocks,
    unmask_translated_prose,
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


def test_mask_roundtrip():
    md = "Hello\n\n```\ncode\n```\n"
    masked, preserved = mask_non_prose_for_translate(md)
    assert "⟦YDBDOC_BLOCK_0⟧" in masked
    assert "code" not in masked
    restored = unmask_translated_prose(masked.replace("Hello", "Hi"), preserved)
    assert "```" in restored
    assert "code" in restored
    assert "Hi" in restored


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
