"""Per-unit EN postprocess after translate."""

from ydbdoc_review.pipeline_v2 import _postprocess_en_fragment


def test_postprocess_en_fragment_fixes_uuid_space():
    raw = "ydb admin cluster bootstrap -- uuid <string>\n"
    assert "--uuid" in _postprocess_en_fragment(raw)
    assert "-- uuid" not in _postprocess_en_fragment(raw)
