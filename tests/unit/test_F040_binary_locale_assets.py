"""F-040: copy locale binary assets byte-for-byte along paired paths."""

from pathlib import Path

from ydbdoc_review.validation.locale_assets import copy_locale_assets_for_pair


def test_F040_copy_update(tmp_path: Path):
    source_file = "ydb/docs/ru/core/topic/page.md"
    source = tmp_path / "ydb/docs/ru/core/topic/_assets/diagram-rub.png"
    target = tmp_path / "ydb/docs/en/core/topic/_assets/diagram.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG\r\n\x00source-v1")
    body = "![Diagram](_assets/diagram-rub.png)\n"

    assert copy_locale_assets_for_pair(
        str(tmp_path), source_file=source_file, source_text=body
    ) == ["ydb/docs/en/core/topic/_assets/diagram.png"]
    assert target.read_bytes() == b"\x89PNG\r\n\x00source-v1"

    source.write_bytes(b"\x89PNG\r\n\x00source-v2")
    assert copy_locale_assets_for_pair(
        str(tmp_path),
        source_file=source_file,
        source_text=body,
        changed_paths={"ydb/docs/ru/core/topic/_assets/diagram-rub.png"},
    ) == [
        "ydb/docs/en/core/topic/_assets/diagram.png"
    ]
    assert target.read_bytes() == b"\x89PNG\r\n\x00source-v2"


def test_F040_keep_existing(tmp_path: Path):
    source_file = "ydb/docs/ru/core/topic/page.md"
    source = tmp_path / "ydb/docs/ru/core/topic/_assets/diagram-rub.png"
    target = tmp_path / "ydb/docs/en/core/topic/_assets/diagram.png"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_bytes(b"image-bytes")
    target.write_bytes(b"existing-image-bytes")
    body = "![Original image label](_assets/diagram-rub.png)\n"

    assert copy_locale_assets_for_pair(
        str(tmp_path), source_file=source_file, source_text=body
    ) == []
    assert target.name == "diagram.png"
    assert target.read_bytes() == b"existing-image-bytes"
    assert not (target.parent / "diagram-rub.png").exists()
