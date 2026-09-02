"""§6.229: tip+overlay docs reader for merged-PR late repair / link gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ydbdoc_review.github.git_ops import read_text_at_ref
from ydbdoc_review.github.workflow import _final_tree_reader
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.validation.en_link_targets import apply_en_link_target_checks


def _git(repo: str, *args: str) -> str:
    return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    ru = repo / "ydb" / "docs" / "ru"
    ru.mkdir(parents=True)
    (ru / "a.md").write_text("# RU\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def test_final_tree_reader_prefers_tip_for_non_overlay(git_repo: str):
    """Stale merge checkout must not hide tip-only EN siblings."""
    en = Path(git_repo) / "ydb" / "docs" / "en" / "core"
    old = en / "devops" / "deployment-options" / "manual"
    old.mkdir(parents=True)
    (old / "node-authorization.md").write_text(
        "## Enabling {#vklyuchenie-rezhima}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "merge-era path"], cwd=git_repo, check=True)
    merge_sha = _git(git_repo, "rev-parse", "HEAD")

    # Tip moved the page; merge checkout no longer has it.
    new = en / "devops" / "concepts"
    new.mkdir(parents=True)
    (new / "node-authorization.md").write_text(
        "## Enabling the node authentication and authorization mode\n",
        encoding="utf-8",
    )
    (old / "node-authorization.md").unlink()
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "tip move"], cwd=git_repo, check=True)
    tip_sha = _git(git_repo, "rev-parse", "HEAD")

    subprocess.run(["git", "checkout", "-q", merge_sha], cwd=git_repo, check=True)
    assert not (new / "node-authorization.md").exists()

    read = _final_tree_reader(git_repo, tip_sha, overlay_paths=set())
    tip_page = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    text = read(tip_page)
    assert text is not None
    assert "Enabling the node authentication" in text


def test_late_repair_does_not_rewrite_tip_href_against_stale_merge(git_repo: str):
    """#40385: preserved tip EN must not be retargeted to merge-era paths."""
    en = Path(git_repo) / "ydb" / "docs" / "en" / "core"
    cfg = en / "reference" / "configuration"
    cfg.mkdir(parents=True)
    old = en / "devops" / "deployment-options" / "manual"
    old.mkdir(parents=True)
    page = cfg / "client_certificate_authorization.md"
    tip_href = (
        "../../devops/concepts/node-authorization.md"
        "#enabling-the-node-authentication-and-authorization-mode"
    )
    stale_href = (
        "../../devops/deployment-options/manual/node-authorization.md"
        "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    )
    page.write_text(f"See [nodes]({stale_href}).\n", encoding="utf-8")
    (old / "node-authorization.md").write_text(
        "## Enabling {#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "merge checkout"], cwd=git_repo, check=True)
    merge_sha = _git(git_repo, "rev-parse", "HEAD")

    new = en / "devops" / "concepts"
    new.mkdir(parents=True)
    (new / "node-authorization.md").write_text(
        "## Enabling the node authentication and authorization mode\n",
        encoding="utf-8",
    )
    tip_body = f"See [nodes]({tip_href}).\n"
    page.write_text(tip_body, encoding="utf-8")
    (old / "node-authorization.md").unlink()
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "tip"], cwd=git_repo, check=True)
    tip_sha = _git(git_repo, "rev-parse", "HEAD")

    # Action checkout = merge; then we write tip-preserved EN as overlay.
    subprocess.run(["git", "checkout", "-q", merge_sha], cwd=git_repo, check=True)
    rel = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    page.write_text(tip_body, encoding="utf-8")

    read = _final_tree_reader(git_repo, tip_sha, {rel})
    assert read(rel) == tip_body
    assert page.read_text(encoding="utf-8") == tip_body


def test_en_link_gate_uses_tip_targets_for_preserved_overlay(git_repo: str):
    """Gate must resolve tip-only siblings when validating preserved tip EN."""
    en = Path(git_repo) / "ydb" / "docs" / "en" / "core"
    cfg = en / "reference" / "configuration"
    cfg.mkdir(parents=True)
    old = en / "devops" / "deployment-options" / "manual"
    old.mkdir(parents=True)
    page_rel = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    page = Path(git_repo) / page_rel
    tip_href = (
        "../../devops/concepts/node-authorization.md"
        "#enabling-the-node-authentication-and-authorization-mode"
    )
    page.write_text("stale merge body\n", encoding="utf-8")
    (old / "node-authorization.md").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "merge"], cwd=git_repo, check=True)

    new = en / "devops" / "concepts"
    new.mkdir(parents=True)
    (new / "node-authorization.md").write_text(
        "## Enabling the node authentication and authorization mode\n",
        encoding="utf-8",
    )
    tip_body = f"See [nodes]({tip_href}).\n"
    page.write_text(tip_body, encoding="utf-8")
    (old / "node-authorization.md").unlink()
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "tip"], cwd=git_repo, check=True)
    tip_sha = _git(git_repo, "rev-parse", "HEAD")

    subprocess.run(["git", "checkout", "-q", f"{tip_sha}^"], cwd=git_repo, check=True)
    page.write_text(tip_body, encoding="utf-8")

    pair = DocPair(
        ru_path=page_rel.replace("/docs/en/", "/docs/ru/", 1),
        en_path=page_rel,
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=page_rel,
        source_lang="ru",
        target_lang="en",
        summary="preserve",
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text=tip_body)]
    )
    broken = apply_en_link_target_checks(
        result,
        repo_path=git_repo,
        en_md_paths={page_rel},
        baseline_read=lambda p: read_text_at_ref(git_repo, tip_sha, p),
        docs_read=_final_tree_reader(git_repo, tip_sha, {page_rel}),
    )
    assert broken == []
