import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.pr import PullRequestContext
from ydbdoc_review.github.provenance import (
    AuthorityRoute,
    FrozenAuthoritySelection,
    RuAuthority,
)
from ydbdoc_review.github.workflow import run_doc_translate
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.navigation.dependency_budget import MarkdownDependencyBudget
from ydbdoc_review.navigation.scope_planner import TranslationScopePlan
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.translation_preflight import (
    PreflightResult,
    preflight_translation,
)
from ydbdoc_review.pipeline.types import (
    PublicationImpact,
)

ROOT_EN = "ydb/docs/en/core/toc_p.yaml"
SECURITY_RU = "ydb/docs/ru/core/security/toc_p.yaml"
SECURITY_EN = "ydb/docs/en/core/security/toc_p.yaml"
AUTH_RU = "ydb/docs/ru/core/security/authentication.md"
CACHE_RU = "ydb/docs/ru/core/security/caching-authentication-results.md"
TOKEN_RU = "ydb/docs/ru/core/security/_assets/user-token.md"
EXPIRY_RU = "ydb/docs/ru/core/security/_assets/user-token-expiration.md"


def _security_plan(*, budget: MarkdownDependencyBudget | None = None):
    docs = frozenset({AUTH_RU, CACHE_RU, TOKEN_RU, EXPIRY_RU})
    return TranslationScopePlan(
        doc_ru_paths=docs,
        doc_from_diff=frozenset({AUTH_RU}),
        doc_from_main=docs - {AUTH_RU},
        nav_ru_paths=frozenset({SECURITY_RU}),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset({SECURITY_RU}),
        dependency_budget=budget or MarkdownDependencyBudget(),
    )


def _security_files():
    ru = {
        SECURITY_RU: (
            "items:\n"
            "- name: Auth RU\n"
            "  href: authentication.md\n"
            "  items:\n"
            "  - name: Cache RU\n"
            "    href: caching-authentication-results.md\n"
        ),
        AUTH_RU: "# Auth\n",
        CACHE_RU: (
            "# Cache\n\n"
            "{% include [Token](_assets/user-token.md) %}\n\n"
            "{% include [Expiry](_assets/user-token-expiration.md) %}\n"
        ),
        TOKEN_RU: "Token fragment.\n",
        EXPIRY_RU: "Expiry fragment.\n",
    }
    ru_base = {
        SECURITY_RU: (
            "items:\n- name: Auth RU\n  href: authentication.md\n"
        ),
    }
    en = {
        ROOT_EN: (
            "items:\n"
            "- name: Security\n"
            "  include:\n"
            "    path: security/toc_p.yaml\n"
            "    mode: link\n"
        ),
        SECURITY_EN: (
            "items:\n- name: Authentication\n  href: authentication.md\n"
        ),
        AUTH_RU.replace("/ru/", "/en/"): "# Authentication\n",
    }
    return ru, ru_base, en


def _run(plan, ru, ru_base, en):
    return preflight_translation(
        plan,
        read_ru=ru.get,
        read_ru_base=ru_base.get,
        read_en_base=en.get,
    )


def test_missing_source_is_a_preflight_blocker():
    path = "ydb/docs/ru/core/security/authentication.md"
    plan = TranslationScopePlan(
        doc_ru_paths=frozenset({path}),
        doc_from_diff=frozenset({path}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(),
    )

    result = preflight_translation(
        plan,
        read_ru=lambda path: None,
        read_ru_base=lambda path: None,
        read_en_base=lambda path: None,
    )

    assert any(
        "missing_source" in message and path in message
        for message in result.blockers
    )


def test_valid_mixed_toc_and_include_topology_is_ready_for_model_work():
    ru, ru_base, en = _security_files()

    result = _run(_security_plan(), ru, ru_base, en)

    assert result.blockers == ()
    assert any("generated_output" in check for check in result.deferred_checks)


def test_task1_child_loss_is_a_named_preflight_blocker(monkeypatch):
    ru, ru_base, en = _security_files()
    monkeypatch.setattr(
        "ydbdoc_review.pipeline.translation_preflight.merge_en_toc_yaml",
        lambda en_main, *_args, **_kwargs: en_main,
        raising=False,
    )

    result = _run(_security_plan(), ru, ru_base, en)

    assert any(
        "orphan_toc_page" in blocker and "caching-authentication-results.md" in blocker
        for blocker in result.blockers
    )


def test_missing_mandatory_include_source_blocks_before_translation():
    ru, ru_base, en = _security_files()
    ru.pop(TOKEN_RU)

    result = _run(_security_plan(), ru, ru_base, en)

    assert any(
        "missing_include_source" in blocker and TOKEN_RU in blocker
        for blocker in result.blockers
    )


def test_disconnected_planned_sidebar_is_not_promoted_to_a_root():
    ru, ru_base, en = _security_files()
    en[ROOT_EN] = "items:\n- name: Other\n  href: ../other.md\n"
    en["ydb/docs/en/other.md"] = "# Other\n"

    result = _run(_security_plan(), ru, ru_base, en)

    assert any(
        "disconnected_sidebar" in blocker and SECURITY_EN in blocker
        for blocker in result.blockers
    )


def test_budget_warning_alone_is_not_a_preflight_blocker():
    ru, ru_base, en = _security_files()
    budget = MarkdownDependencyBudget(limit=0)
    assert not budget.admit("ydb/docs/ru/core/unrelated.md")

    result = _run(_security_plan(budget=budget), ru, ru_base, en)

    assert budget.warnings
    assert result.blockers == ()


def test_deleted_source_does_not_need_to_exist_at_r():
    path = "ydb/docs/ru/core/security/removed.md"
    plan = TranslationScopePlan(
        doc_ru_paths=frozenset({path}),
        doc_from_diff=frozenset({path}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(),
        doc_deleted=frozenset({path}),
    )

    result = preflight_translation(
        plan,
        read_ru=lambda _path: None,
        read_ru_base=lambda _path: None,
        read_en_base=lambda _path: None,
    )

    assert result.blockers == ()


@pytest.fixture
def workflow_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=repo,
        check=True,
    )
    source = repo / AUTH_RU
    source.parent.mkdir(parents=True)
    source.write_text("# Auth\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True)
    return str(repo)


def _workflow_env() -> dict[str, str]:
    return {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "folder",
        "YDBDOC_YC_API_KEY": "key",
        "GITHUB_TOKEN": "token",
        "GITHUB_PUSH_TOKEN": "push-token",
        "YDBDOC_SKIP_OPS_GATES": "1",
    }


def _patch_workflow_prefix(monkeypatch, repo: str, plan: TranslationScopePlan):
    sha = subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        text=True,
    ).strip()
    ctx = PullRequestContext(
        owner="o",
        repo="r",
        number=51079,
        title="Source",
        head_ref="feature",
        head_sha=sha,
        head_repo_full_name="o/r",
        head_repo_https_url="https://github.com/o/r.git",
        base_ref="main",
        base_sha=sha,
    )
    authority = RuAuthority(
        source_repo="o/r",
        source_pr=51079,
        source_base_sha=sha,
        source_head_sha=sha,
        baseline_sha=sha,
        ru_sha=sha,
        mode=RuAuthorityMode.CURRENT,
    )
    selection = FrozenAuthoritySelection(
        authority=authority,
        route=AuthorityRoute.OPEN_SAME_REPO,
        checkout_sha=sha,
        prepare_parent_sha=sha,
    )
    readers = ({AUTH_RU: "# Auth\n"}.get, {}.get, {}.get)
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.begin_ops_job",
        lambda **_kwargs: (None, GateResult(ok=True), None),
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.pull_request_context", lambda *_args: ctx
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.freeze_ru_authority",
        lambda *_args, **_kwargs: selection,
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow._snapshot_destination_lease",
        lambda *_args: MagicMock(expected_sha=None),
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.list_pr_file_changes_api",
        lambda *_args: [(AUTH_RU, "modified")],
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.make_repo_scope_readers",
        lambda *_args, **_kwargs: readers,
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.plan_translation_scope",
        lambda *_args, **_kwargs: plan,
    )
    return readers


def test_workflow_preflight_blockers_withhold_without_model_client(
    workflow_repo: str,
    monkeypatch,
):
    plan = TranslationScopePlan(
        doc_ru_paths=frozenset({AUTH_RU}),
        doc_from_diff=frozenset({AUTH_RU}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(),
    )
    readers = _patch_workflow_prefix(monkeypatch, workflow_repo, plan)
    ops_ctx = MagicMock()
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.begin_ops_job",
        lambda **_kwargs: (ops_ctx, GateResult(ok=True), None),
    )
    preflight = MagicMock(
        return_value=PreflightResult(
            blockers=(f"missing_source: {AUTH_RU}",),
            deferred_checks=(),
        )
    )
    make_client = MagicMock(
        side_effect=AssertionError("preflight must run before model setup")
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.preflight_translation", preflight, raising=False
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.create_llm_client", make_client
    )
    post_comment = MagicMock(return_value="https://github.com/o/r/issues/51079#comment")
    finish_ops = MagicMock()
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow._safe_post_issue_comment", post_comment
    )
    monkeypatch.setattr("ydbdoc_review.github.workflow.finish_ops_job", finish_ops)

    result = run_doc_translate(
        repo_path=workflow_repo,
        github_repo="o/r",
        pr_number=51079,
        merge_base_with="HEAD",
        dry_run=False,
        config=load_config(env=_workflow_env()),
    )

    preflight.assert_called_once()
    assert preflight.call_args.kwargs["read_ru"] is readers[0]
    assert preflight.call_args.kwargs["read_ru_base"] is readers[2]
    assert preflight.call_args.kwargs["read_en_base"] is readers[1]
    make_client.assert_not_called()
    assert result.pr_result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
    assert f"missing_source: {AUTH_RU}" in result.pr_result.completeness_gaps
    assert result.source_comment_url == "https://github.com/o/r/issues/51079#comment"
    post_comment.assert_called_once()
    finish_ops.assert_called_once_with(ops_ctx, status="failed", cost_rub=0.0)


def test_valid_preflight_reaches_translator_but_does_not_approve_bad_output(
    workflow_repo: str,
    monkeypatch,
):
    plan = TranslationScopePlan(
        doc_ru_paths=frozenset({AUTH_RU}),
        doc_from_diff=frozenset({AUTH_RU}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset(),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset(),
    )
    _patch_workflow_prefix(monkeypatch, workflow_repo, plan)
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.preflight_translation",
        lambda *_args, **_kwargs: PreflightResult(
            blockers=(),
            deferred_checks=("generated_output_validation",),
        ),
        raising=False,
    )
    backend = MagicMock()
    backend.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1),
    )
    cfg = load_config(env=_workflow_env())
    fake_client = YandexLLMClient(
        folder_id="folder",
        api_key="key",
        llm=cfg.llm,
        client=backend,
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.create_llm_client", lambda _cfg: fake_client
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.build_en_toc_reachable_from_repo",
        lambda *_args, **_kwargs: frozenset({AUTH_RU.replace("/ru/", "/en/")}),
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.read_text_at_commit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.load_pair_contents",
        lambda *_args, **_kwargs: [
            PairContent(
                pair=DocPair(
                    ru_path=AUTH_RU,
                    en_path=AUTH_RU.replace("/ru/", "/en/"),
                    ru_changed=True,
                ),
                ru_text="Привет.\n",
                en_text=None,
            )
        ],
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "ydbdoc_review.github.workflow.completeness_gaps",
        lambda *_args, **_kwargs: [],
    )

    result = run_doc_translate(
        repo_path=workflow_repo,
        github_repo="o/r",
        pr_number=51079,
        merge_base_with="HEAD",
        dry_run=True,
        config=cfg,
    )

    assert backend.chat.completions.create.call_count > 0
    assert result.pr_result.publication_impact == PublicationImpact.WITHHOLD_INCOMPLETE
