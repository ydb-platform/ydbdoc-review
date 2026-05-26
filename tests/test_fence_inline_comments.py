"""Inline # comments in fenced blocks (YAML host lines)."""

from ydbdoc_review.fence_comments import (
    inline_hash_comment_tail,
    translate_fence_comments,
)
from ydbdoc_review.pipeline_v2 import (
    _apply_translated_fence_comments,
    _fence_comment_rows,
)


def test_inline_hash_comment_tail_on_yaml_host_line():
    line = "    - host: static-node-1.ydb-cluster.com #FQDN ВМ"
    tail = inline_hash_comment_tail(line)
    assert tail is not None
    prefix, body = tail
    assert prefix.endswith(".com ")
    assert "FQDN" in body
    assert "ВМ" in body


def test_inline_hash_skips_line_starting_with_hash():
    assert inline_hash_comment_tail("# full line comment") is None


def test_fence_comment_rows_includes_inline_hash():
    fence = (
        "```yaml\n"
        "hosts:\n"
        "    - host: static-node-1.ydb-cluster.com #FQDN ВМ\n"
        "```"
    )
    rows = _fence_comment_rows(fence, source_lang="Russian")
    assert len(rows) == 1
    assert rows[0][2] == "FQDN ВМ"


def test_apply_translated_fence_comments_inline_hash():
    fence = "    - host: x.ydb-cluster.com #FQDN ВМ\n"
    out = _apply_translated_fence_comments(
        fence,
        [{"line": 0, "marker": "#", "text": "VM FQDN"}],
    )
    assert "VM FQDN" in out
    assert "ВМ" not in out


def test_translate_fence_comments_inline_hash():
    fence = "    - host: x.com #FQDN ВМ\n"

    def fake_translate(body: str) -> str:
        assert "ВМ" in body
        return "VM FQDN"

    out = translate_fence_comments(fence, fake_translate, only_if_cyrillic=True)
    assert "VM FQDN" in out
    assert "ВМ" not in out
