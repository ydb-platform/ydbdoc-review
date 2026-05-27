from ydbdoc_review.annotated_translate import (
    AnnotatedChunk,
    build_annotated_chunks,
    merge_copy_regions_from_source,
    refine_tab_regions,
)
from ydbdoc_review.document_structure import analyze_document_structure


def test_chunks_never_split_mid_fence():
    ru = (
        "Text before.\n\n"
        "```bash\n"
        "echo hello\n"
        "echo world\n"
        "```\n\n"
        "Text after.\n"
    )
    regions = analyze_document_structure(ru, source_is_russian=True)
    chunks = build_annotated_chunks(ru, regions, max_chars=50)
    for ch in chunks:
        body = "\n".join(ru.splitlines()[ch.start_line - 1 : ch.end_line])
        opens = body.count("```")
        assert opens % 2 == 0, f"chunk {ch.index} splits a fence: {body!r}"


def test_config_tabs_chunk_is_copy_only():
    ru = (
        "Intro.\n\n"
        "{% list tabs %}\n\n"
        "- mirror-3-dc-3nodes\n\n"
        "  ```yaml\n"
        "  x: 1\n"
        "  ```\n\n"
        "{% endlist %}\n\n"
        "Outro.\n"
    )
    regions = refine_tab_regions(
        ru, analyze_document_structure(ru, source_is_russian=True)
    )
    chunks = build_annotated_chunks(ru, regions)
    tabs_chunks = [
        c for c in chunks if any(r.kind == "tabs" for r in c.regions)
    ]
    assert len(tabs_chunks) == 1
    assert tabs_chunks[0].copy_only()


def test_merge_copy_regions_restores_fence():
    ru = "```yaml\nkey: val\n```\n"
    en = "```yaml\nkey: CHANGED\n```\n"
    regions = analyze_document_structure(ru, source_is_russian=False)
    merged = merge_copy_regions_from_source(
        ru, en, regions, chunk_start_line=1
    )
    assert "CHANGED" not in merged
    assert "key: val" in merged
