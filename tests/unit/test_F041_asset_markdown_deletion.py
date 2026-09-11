from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.github.workflow import _apply_results_to_disk
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair, is_docs_markdown
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.validation.toc_targets import (
    _is_toc_orphan_exempt,
    check_orphan_pages_for_locale,
)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return repo


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_F041_asset_markdown(tmp_path: Path):
    repo = _init_repo(tmp_path)
    root_toc = "ydb/docs/en/core/toc_p.yaml"
    asset = "ydb/docs/en/core/_assets/diagram.md"
    include = "ydb/docs/en/core/_includes/snippet.md"
    _write(repo, root_toc, "items:\n- name: Diagram\n  href: _assets/diagram.md\n")
    _write(repo, asset, "# Diagram\n")
    _write(repo, include, "shared\n")

    orphans = check_orphan_pages_for_locale(
        {asset, include},
        repo_path=str(repo),
        locale="en",
        pending_md_texts={asset: "# Diagram\n", include: "shared\n"},
    )

    assert asset not in orphans
    assert include not in orphans
    assert is_docs_markdown(asset, "ydb/docs")
    assert is_docs_markdown(include, "ydb/docs")
    assert not _is_toc_orphan_exempt(asset, docs_root="ydb/docs", locale="en")
    assert _is_toc_orphan_exempt(include, docs_root="ydb/docs", locale="en")


def test_F041_used_delete(tmp_path: Path):
    repo = _init_repo(tmp_path)
    target = "ydb/docs/en/core/_assets/diagram.md"
    _write(repo, target, "# Diagram\n")
    _write(repo, "ydb/docs/en/core/page.md", "{% include [diagram](./_assets/diagram.md) %}\n")
    _write(repo, "ydb/docs/en/core/public.md", "[diagram](https://ydb.tech/docs/en/core/_assets/diagram.md)\n")

    pair = DocPair(
        ru_path="ydb/docs/ru/core/_assets/diagram.md",
        en_path=target,
        ru_deleted=True,
    )
    plan = PairPlan(
        pair=pair,
        action="delete_en",
        source_path=pair.ru_path,
        target_path=target,
        source_lang="ru",
        target_lang="en",
    )
    run = PairRunResult(plan=plan, deleted=True)
    result = PRTranslationResult(pair_results=[run])

    touched = _apply_results_to_disk(str(repo), result, dry_run=False)

    assert (repo / target).is_file()
    assert touched.deleted == []
    assert run.deleted is False
    assert run.error is not None
    assert run.error.startswith("resource_delete_blocked:")
