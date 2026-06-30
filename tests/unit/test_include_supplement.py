"""Tests for include pair supplementation (§6.80)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.pipeline.include_supplement import supplement_include_pairs
from ydbdoc_review.pipeline.pairs import DocPair


def _init_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return str(repo)


def test_supplement_include_pairs_adds_child_fragment(tmp_path: Path):
    repo = _init_repo(tmp_path)
    root = Path(repo) / "ydb" / "docs" / "ru" / "core" / "cli" / "export-import" / "_includes"
    root.mkdir(parents=True)
    (root / "export-s3.md").write_text(
        "Title\n\n{% include [extra](export-additional-params.md) %}\n",
        encoding="utf-8",
    )
    (root / "export-additional-params.md").write_text(
        "## Extra\n\n- `--foo`\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    pairs = [
        DocPair(
            ru_path=str(
                Path("ydb/docs/ru/core/cli/export-import/_includes/export-s3.md")
            ).replace("\\", "/"),
            en_path=str(
                Path("ydb/docs/en/core/cli/export-import/_includes/export-s3.md")
            ).replace("\\", "/"),
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
