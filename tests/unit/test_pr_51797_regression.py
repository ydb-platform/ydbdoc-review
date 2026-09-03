from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.pr import (
    PullRequestContext,
    load_pair_contents,
    source_pr_scope_changes,
)
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
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.en_link_targets import apply_en_link_target_checks

AUTH_RU = "ydb/docs/ru/core/security/authentication.md"
AUTH_EN = AUTH_RU.replace("/ru/", "/en/")
OWNER_RU = "ydb/docs/ru/core/reference/ydb-cli/_includes/connect.md"
OWNER_EN = OWNER_RU.replace("/ru/", "/en/")
HREF = "../reference/ydb-cli/connect.md#tls"
MERGE_SHA = "d9fc9f993eb7fbade94da40c7c666178abb93170"
API_CHANGES = [
    ("ydb/docs/ru/core/reference/configuration/client_certificate_authorization.md", "modified"),
    ("ydb/docs/ru/core/reference/configuration/monitoring_config.md", "modified"),
    ("ydb/docs/ru/core/reference/configuration/tls.md", "modified"),
    (AUTH_RU, "modified"),
    ("ydb/docs/ru/core/security/index.md", "modified"),
]


def _put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, *, commit_merge: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _put(repo, AUTH_RU, f"# Authentication\n\n[TLS one]({HREF})\n[TLS two]({HREF})\n")
    _put(repo, AUTH_EN, "# Authentication\n")
    include = "{% include [connect](_includes/connect.md) %}\n"
    for locale in ("ru", "en"):
        _put(repo, f"ydb/docs/{locale}/core/reference/ydb-cli/connect.md", include)
    _put(
        repo,
        OWNER_RU,
        "### Параметры аутентификации {#authentication}\n"
        "{% include [auth/options.md](auth/options.md) %}\n"
        "### Параметры TLS-соединения {#tls}\n"
        "{% include [auth/options_client_cert.md](auth/options_client_cert.md) %}\n"
        "{% include [env.md](auth/env.md) %}\n",
    )
    _put(
        repo,
        OWNER_EN,
        "### Authentication parameters {#authentication}\n"
        "{% include [auth/options.md](auth/options.md) %}\n"
        "### TLS connection parameters\n"
        "{% include [auth/options_client_cert.md](auth/options_client_cert.md) %}\n"
        "{% include [env.md](auth/env.md) %}\n",
    )
    for path, _kind in API_CHANGES:
        if path != AUTH_RU:
            _put(repo, path, f"# {Path(path).stem}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    _put(
        repo,
        AUTH_RU,
        f"# Authentication\n\nUpdated surrounding content.\n\n"
        f"[TLS one]({HREF})\n[TLS two]({HREF})\n",
    )
    for path, _kind in API_CHANGES:
        if path != AUTH_RU:
            _put(repo, path, f"# {Path(path).stem}\n\nUpdated.\n")
    if commit_merge:
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "merged PR 40385 fixture")
    return repo


def _fake_file_result(_harness, state, _ctx) -> FileTranslationResult:
    final = (
        "### Authentication parameters {#authentication}\n"
        "{% include [auth/options.md](auth/options.md) %}\n"
        "### TLS connection parameters\n"
        "{% include [auth/options_client_cert.md](auth/options_client_cert.md) %}\n"
        "{% include [env.md](auth/env.md) %}\n"
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


def test_pr_40385_merged_five_api_paths_load_six_pairs_before_translation(tmp_path: Path):
    repo = _repo(tmp_path, commit_merge=True)
    merge_sha = _git_output(repo, "rev-parse", "HEAD")
    parent_sha = _git_output(repo, "rev-parse", "HEAD^")
    _put(repo, AUTH_EN, "# Authentication\n\nCurrent upstream-tip EN.\n")
    _git(repo, "add", AUTH_EN)
    _git(repo, "commit", "-qm", "distinct upstream main tip")
    upstream_main_ref = _git_output(repo, "rev-parse", "HEAD")
    assert len({merge_sha, parent_sha, upstream_main_ref}) == 3
    ctx = PullRequestContext(
        owner="ydb-platform", repo="ydb", number=40385, title="docs",
        head_ref="docs/source", head_sha="source-head",
        head_repo_full_name="ydb-platform/ydb",
        head_repo_https_url="https://github.com/ydb-platform/ydb.git",
        base_ref="main", merged=True, merge_commit_sha=MERGE_SHA,
    )
    noisy_git_changes = [("ydb/docs/ru/core/noisy-local-only.md", "modified")]
    changes = source_pr_scope_changes(ctx, noisy_git_changes, API_CHANGES)
    assert changes == API_CHANGES
    assert noisy_git_changes[0] not in changes

    read_ru, read_en_base, read_ru_base = make_repo_scope_readers(
        str(repo), upstream_main_ref, ru_content_ref=merge_sha, ru_base_ref=f"{merge_sha}^",
    )
    assert (read_ru(AUTH_RU) or "").count(HREF) == 2
    assert (read_ru_base(AUTH_RU) or "").count(HREF) == 2
    plan = plan_translation_scope(
        changes, read_ru=read_ru, read_en_base=read_en_base, read_ru_base=read_ru_base,
    )
    expected_diff = frozenset(path for path, _kind in API_CHANGES)
    assert plan.doc_ru_paths == expected_diff | {OWNER_RU}
    assert plan.doc_from_diff == expected_diff
    assert plan.doc_from_main == frozenset({OWNER_RU})
    assert plan.nav_ru_paths == frozenset()
    assert plan.nav_from_diff == frozenset()
    assert plan.nav_from_main == frozenset()

    pairs = doc_pairs_from_plan(plan)
    assert len(pairs) == 6
    contents = load_pair_contents(
        str(repo), pairs, merge_base_with=upstream_main_ref,
        ru_content_ref=merge_sha, ru_base_ref=f"{merge_sha}^",
    )
    assert len(contents) == 6
    owner_content = next(content for content in contents if content.pair.ru_path == OWNER_RU)
    assert owner_content.pair.en_path == OWNER_EN
    assert "{#tls}" in (owner_content.ru_text or "")
    assert "{#tls}" not in (owner_content.en_text or "")

    with patch("ydbdoc_review.pipeline.orchestrator.run_pr_translation") as translate:
        translate(contents, MagicMock(), load_glossary())
    loaded = translate.call_args.args[0]
    assert len(loaded) == 6
    assert any(item.pair.ru_path == OWNER_RU and item.pair.en_path == OWNER_EN for item in loaded)


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


def test_pr_40385_full_post_translate_link_contract_clears_auth_failures(tmp_path: Path):
    """R-GL-11: exercise the production post-translate lifecycle, not helpers alone."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    auth_ru = (
        "[Security](../reference/configuration/auth_config.md#security-auth)\n"
        "[Certificate](../reference/configuration/auth_config.md#certificate-auth-config)\n"
    )
    auth_en_tip = (
        "[Security](../reference/configuration/security_config.md#security-auth)\n"
        "[Certificate](../reference/configuration/auth_config.md#certificate-auth-config)\n"
    )
    auth_config_ru = (
        "# auth_config\n\n"
        "## Настройка аутентификации по сертификату {#certificate-auth-config}\n"
    )
    auth_config_en = "# auth_config\n\n## Certificate authentication configuration\n"
    security_config_en = "# security_config\n\n## Authentication {#security-auth}\n"
    for rel, text in (
        (AUTH_RU, auth_ru),
        (AUTH_EN, auth_en_tip),
        ("ydb/docs/ru/core/reference/configuration/auth_config.md", auth_config_ru),
        ("ydb/docs/en/core/reference/configuration/auth_config.md", auth_config_en),
        ("ydb/docs/en/core/reference/configuration/security_config.md", security_config_en),
    ):
        _put(repo, rel, text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "source snapshot")
    source_ref = _git_output(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-qm", "upstream tip")
    tip_ref = _git_output(repo, "rev-parse", "HEAD")

    auth_pair = DocPair(ru_path=AUTH_RU, en_path=AUTH_EN, ru_changed=True)
    owner_ru = "ydb/docs/ru/core/reference/configuration/auth_config.md"
    owner_en = owner_ru.replace("/ru/", "/en/")
    owner_pair = DocPair(ru_path=owner_ru, en_path=owner_en, ru_changed=True)
    contents = load_pair_contents(
        str(repo), [auth_pair, owner_pair], merge_base_with=tip_ref,
        ru_content_ref=source_ref,
    )

    def fake_result(_harness, state, _ctx):
        final = auth_ru if state.file_path == AUTH_RU else auth_config_en
        return FileTranslationResult(
            file_path=state.file_path, final_text=final, segments_count=1,
            verdict="ok", prompt_version="test",
        )

    client = MagicMock()
    client.usage_tracker.records = []
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    final_tree = {
        AUTH_EN: auth_en_tip,
        owner_en: auth_config_en,
        "ydb/docs/en/core/reference/configuration/security_config.md": security_config_en,
    }
    with patch("ydbdoc_review.harness.pair.FileHarness.run", fake_result):
        result = run_pr_translation(
            contents, client, load_glossary(), config=cfg,
            docs_text_reader=final_tree.get,
        )
    touched = _apply_results_to_disk(str(repo), result, dry_run=False)
    declared = _declare_exact_ascii_fragment_targets_after_apply(
        str(repo), touched.written, dry_run=False,
        merge_base_with=tip_ref, ru_content_ref=source_ref,
    )
    en_written = set(touched.written) | set(declared)

    auth_after = (repo / AUTH_EN).read_text(encoding="utf-8")
    owner_after = (repo / owner_en).read_text(encoding="utf-8")
    assert "security_config.md#security-auth" in auth_after
    assert "auth_config.md#certificate-auth-config" in auth_after
    assert "{#certificate-auth-config}" in owner_after
    assert apply_en_link_target_checks(
        result, repo_path=str(repo), en_md_paths=en_written
    ) == []
