from ydbdoc_review.document_structure import (
    analyze_document_structure,
    format_region_plan,
    split_by_h3_sections,
)
from ydbdoc_review.file_translate import build_translate_chunks as ft_chunks


def test_analyze_detects_fence_table_tabs():
    text = (
        "Intro line\n\n"
        "| A | B |\n"
        "| - | - |\n"
        "| 1 | 2 |\n\n"
        "```yaml\n"
        "key: 1\n"
        "```\n\n"
        "{% list tabs %}\n"
        "- OSS\n"
        "Note\n"
        "```bash\n"
        "echo x\n"
        "```\n"
        "{% endlist %}\n"
    )
    regions = analyze_document_structure(text)
    kinds = [r.kind for r in regions]
    assert "prose" in kinds
    assert "table" in kinds
    assert "fence" in kinds
    assert "tabs" in kinds
    plan = format_region_plan(regions)
    assert "Lines" in plan


def test_build_chunks_respects_region_boundaries():
    text = "line\n\n```\nx\n```\n\ntail\n"
    regions = analyze_document_structure(text)
    chunks = ft_chunks(text, regions, max_chars=50)
    assert len(chunks) >= 1
    assert all(c.source_text for c in chunks)


def test_split_by_h3():
    text = "pre\n\n### One\na\n\n### Two\nb\n"
    secs = split_by_h3_sections(text)
    assert 0 in secs and 1 in secs and 2 in secs
    assert "### One" in secs[1]
