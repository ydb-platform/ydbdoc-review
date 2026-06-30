"""Tests for include pair supplementation (§6.80)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.pipeline.include_supplement import supplement_include_pairs
from ydbdoc_review.pipeline.pairs import DocPair

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "44880"


def _init_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return str(repo)


def test_supplement_include_pairs_adds_child_without_en_mirror(tmp_path: Path):
    repo = _init_repo(tmp_path)
    ru_root = Path(repo) / "ydb" / "docs" / "ru" / "core" / "cli" / "export-import" / "_includes"
    en_root = Path(repo) / "ydb" / "docs" / "en" / "core" / "cli" / "export-import" / "_includes"
    ru_root.mkdir(parents=True)
    en_root.mkdir(parents=True)
    (ru_root / "export-s3.md").write_text(
        "Title\n\n{% include [extra](export-additional-params.md) %}\n",
        encoding="utf-8",
    )
    (ru_root / "export-additional-params.md").write_text(
        (_FIXTURES / "export-additional-params.ru.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    pairs = [
        DocPair(
            ru_path="ydb/docs/ru/core/cli/export-import/_includes/export-s3.md",
            en_path="ydb/docs/en/core/cli/export-import/_includes/export-s3.md",
            ru_changed=True,
        )
    ]
    out, extra = supplement_include_pairs(
        pairs, repo_path=repo, merge_base_with="HEAD"
    )
    ru_paths = {p.ru_path for p in out}
    assert (
        "ydb/docs/ru/core/cli/export-import/_includes/export-additional-params.md"
        in ru_paths
    )
    assert len(out) == 2
    assert extra


def test_supplement_skips_include_when_en_mirror_exists_on_main(tmp_path: Path):
    repo = _init_repo(tmp_path)
    ru_root = Path(repo) / "ydb" / "docs" / "ru" / "core" / "cli" / "export-import" / "_includes"
    en_root = Path(repo) / "ydb" / "docs" / "en" / "core" / "cli" / "export-import" / "_includes"
    ru_root.mkdir(parents=True)
    en_root.mkdir(parents=True)
    (ru_root / "export-s3.md").write_text(
        "Title\n\n"
        "{% include [existing](existing-snippet.md) %}\n"
        "{% include [new](export-additional-params.md) %}\n",
        encoding="utf-8",
    )
    (ru_root / "existing-snippet.md").write_text("Existing RU\n", encoding="utf-8")
    (en_root / "existing-snippet.md").write_text("Existing EN\n", encoding="utf-8")
    (ru_root / "export-additional-params.md").write_text(
        (_FIXTURES / "export-additional-params.ru.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    pairs = [
        DocPair(
            ru_path="ydb/docs/ru/core/cli/export-import/_includes/export-s3.md",
            en_path="ydb/docs/en/core/cli/export-import/_includes/export-s3.md",
            ru_changed=True,
        )
    ]
    out, _extra = supplement_include_pairs(
        pairs, repo_path=repo, merge_base_with="HEAD"
    )
    ru_paths = {p.ru_path for p in out}
    assert "ydb/docs/ru/core/cli/export-import/_includes/existing-snippet.md" not in ru_paths
    assert (
        "ydb/docs/ru/core/cli/export-import/_includes/export-additional-params.md"
        in ru_paths
    )
    assert len(out) == 2
