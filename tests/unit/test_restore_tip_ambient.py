"""§6.240: tip-ambient EN outside source scope restores to merge-base."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.github.workflow import _restore_out_of_scope_en_from_base


def test_restore_out_of_scope_en_from_base(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "ydb/docs/en/core").mkdir(parents=True)
    ambient = "ydb/docs/en/core/compare-configs.md"
    scoped = "ydb/docs/en/core/tls.md"
    (repo / ambient).write_text("POLLUTED\n", encoding="utf-8")
    (repo / scoped).write_text("SCOPED\n", encoding="utf-8")

    def fake_read_ref(_repo: str, ref: str, path: str) -> str | None:
        assert ref == "origin/main"
        if path == ambient:
            return "MAIN_AMBIENT\n"
        if path == scoped:
            return "MAIN_SCOPED\n"
        return None

    def fake_read(repo_path: str, path: str) -> str | None:
        p = Path(repo_path) / path
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def fake_write(repo_path: str, path: str, text: str) -> None:
        target = Path(repo_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.read_text_at_ref", fake_read_ref
    )
    monkeypatch.setattr("ydbdoc_review.github.workflow.read_text", fake_read)
    monkeypatch.setattr("ydbdoc_review.github.workflow.write_text", fake_write)

    restored = _restore_out_of_scope_en_from_base(
        str(repo),
        changes=[(ambient, "modified"), (scoped, "modified")],
        allowed_en_paths=frozenset({scoped}),
        merge_base_with="origin/main",
        docs_root="ydb/docs",
        dry_run=False,
    )
    assert restored == [ambient]
    assert (repo / ambient).read_text(encoding="utf-8") == "MAIN_AMBIENT\n"
    assert (repo / scoped).read_text(encoding="utf-8") == "SCOPED\n"
