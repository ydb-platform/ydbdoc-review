from ydbdoc_review.fence_comments import translate_fence_comments
from ydbdoc_review.markdown_blocks import split_markdown_blocks, translate_preserving_blocks


def test_translate_fence_comments_line_and_inline():
    fence = "```sql\n-- комментарий\nSELECT 1 -- хвост\n```"
    out = translate_fence_comments(
        fence,
        lambda body: body.replace("комментарий", "comment").replace("хвост", "tail"),
    )
    assert "comment" in out
    assert "tail" in out
    assert "комментарий" not in out
    assert "хвост" not in out
    assert "SELECT 1" in out


def test_translate_preserving_blocks_translates_sql_comments_only():
    md = "```sql\n-- комментарий\nSELECT 1\n```\n"
    out = translate_preserving_blocks(
        md, lambda t: t, lambda c: c.replace("комментарий", "comment")
    )
    assert "comment" in out
    assert "комментарий" not in out
    assert split_markdown_blocks(out)[0].kind == "fence"


def test_fix_factically():
    from ydbdoc_review.translate_postprocess import fix_common_ru_prose_leaks

    assert "effectively" in fix_common_ru_prose_leaks("This is фактически true.")
