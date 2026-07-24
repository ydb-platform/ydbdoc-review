"""Transcript store + chunk helpers."""

from ydbdoc_review.ops.transcripts import (
    InMemoryTranscriptStore,
    chunk_payload,
    join_chunks,
)


def test_chunk_roundtrip():
    data = b"x" * 1000
    parts = chunk_payload(data, size=300)
    assert len(parts) == 4
    assert join_chunks(parts) == data


def test_memory_store():
    store = InMemoryTranscriptStore()
    store.put("run1", "manifest.json", '{"ok": true}')
    store.put("run1", "llm/001-translate-req.json", "{}")
    assert store.exists_run("run1")
    assert not store.exists_run("missing")
    assert store.get("run1", "manifest.json") == b'{"ok": true}'
    assert store.list_keys("run1") == [
        "llm/001-translate-req.json",
        "manifest.json",
    ]
