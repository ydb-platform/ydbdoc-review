from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.pr import load_pair_contents
from ydbdoc_review.github.workflow import (
    TouchedPaths,
    _apply_results_to_disk,
    _declare_exact_ascii_fragment_targets_after_apply,
)
from ydbdoc_review.navigation.scope_planner import (
    doc_pairs_from_plan,
    make_repo_scope_readers,
    plan_translation_scope,
)
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.types import FileTranslationResult
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.en_link_targets import apply_en_link_target_checks

AUTH_RU = "ydb/docs/ru/core/security/authentication.md"
AUTH_EN = AUTH_RU.replace("/ru/", "/en/")
OWNER_RU = "ydb/docs/ru/core/reference/ydb-cli/_includes/connect.md"
OWNER_EN = OWNER_RU.replace("/ru/", "/en/")
HREF = "../reference/ydb-cli/connect.md#tls"


def _put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _put(repo, AUTH_RU, "# Authentication\n")
    _put(repo, AUTH_EN, "# Authentication\n")
    include = "{% include [connect](_includes/connect.md) %}\n"
    for locale in ("ru", "en"):
        _put(repo, f"ydb/docs/{locale}/core/reference/ydb-cli/connect.md", include)
    _put(repo, OWNER_RU, "# Connect\n\n## Options\n\n### Параметры TLS {#tls}\n")
    _put(repo, OWNER_EN, "# Connect\n\n## Options\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    _put(repo, AUTH_RU, f"# Authentication\n\n[TLS]({HREF})\n")
    return repo


def _fake_file_result(_harness, state, _ctx) -> FileTranslationResult:
    final = (
        "# Connect\n\n## Options\n\n### TLS connection parameters\n"
        if state.file_path == OWNER_RU
        else f"# Authentication\n\n[TLS]({HREF})\n"
    )
    return FileTranslationResult(
        file_path=state.file_path, final_text=final, segments_count=1,
        verdict="ok", prompt_version="test",
    )


def _plan_and_load(repo: Path):
    read_ru, read_en, read_ru_base = make_repo_scope_readers(str(repo), "HEAD")
    plan = plan_translation_scope(
        [(AUTH_RU, "modified")], read_ru=read_ru,
        read_en_base=read_en, read_ru_base=read_ru_base,
    )
    pairs = doc_pairs_from_plan(plan)
    return plan, pairs, load_pair_contents(str(repo), pairs, merge_base_with="HEAD")


def _translate(contents):
    client = MagicMock()
    client.usage_tracker.records = []
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    with patch("ydbdoc_review.harness.pair.FileHarness.run", _fake_file_result):
        return run_pr_translation(contents, client, load_glossary(), config=cfg)


def test_pr_40385_queued_connect_include_translates_then_declares_tls(tmp_path: Path):
    repo = _repo(tmp_path)
    plan, pairs, contents = _plan_and_load(repo)
    assert OWNER_RU in plan.doc_from_main
    owner_pair = next(pair for pair in pairs if pair.ru_path == OWNER_RU)
    assert any(content.pair == owner_pair and content.ru_text for content in contents)

    result = _translate(contents)
    owner_run = next(run for run in result.pair_results if run.plan.target_path == OWNER_EN)
    assert owner_run.file_result is not None
    assert "TLS connection parameters" in (owner_run.target_text or "")
    touched = _apply_results_to_disk(str(repo), result, dry_run=False)
    declared = _declare_exact_ascii_fragment_targets_after_apply(
        str(repo), touched.written, dry_run=False
    )
    touched = TouchedPaths(list(dict.fromkeys([*touched.written, *declared])), touched.deleted)
    en_written = {p for p in touched.written if "/docs/en/" in p and p.endswith(".md")}
    assert apply_en_link_target_checks(result, repo_path=str(repo), en_md_paths=en_written) == []
    assert OWNER_EN in touched.written  # branch preparation and commit path input
    assert "### TLS connection parameters {#tls}" in (repo / OWNER_EN).read_text()
    assert HREF in (repo / AUTH_EN).read_text()


def test_pr_40385_real_tip_without_queued_translation_stays_blocked(tmp_path: Path):
    repo = _repo(tmp_path)
    plan, pairs, contents = _plan_and_load(repo)
    assert OWNER_RU in plan.doc_from_main
    assert any(pair.ru_path == OWNER_RU for pair in pairs)
    auth_content = next(content for content in contents if content.pair.ru_path == AUTH_RU)
    result = _translate([auth_content])  # deliberately bypass queued owner pair
    touched = _apply_results_to_disk(str(repo), result, dry_run=False)
    before = (repo / OWNER_EN).read_text()
    assert _declare_exact_ascii_fragment_targets_after_apply(
        str(repo), touched.written, dry_run=False
    ) == []
    assert (repo / OWNER_EN).read_text() == before
    broken = apply_en_link_target_checks(result, repo_path=str(repo), en_md_paths={AUTH_EN})
    assert broken == [AUTH_EN]
    messages = result.pair_results[0].file_result.heuristic_blocking
    assert any("missing fragment: tls" in message for message in messages)
    if broken:
        touched = TouchedPaths([], [])
    assert not touched  # production lifecycle cannot prepare, commit, or push
