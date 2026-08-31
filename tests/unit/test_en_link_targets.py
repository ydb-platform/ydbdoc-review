"""Tests for the post-apply EN link/fragment gate (§6.226)."""

from __future__ import annotations

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.validation.en_link_targets import (
    apply_en_link_target_checks,
    check_en_page_link_targets,
    list_declared_fragments,
)


def test_list_declared_fragments_includes_en_auto_slug():
    md = "## Enabling the node authentication and authorization mode\n\nBody.\n"
    assert list_declared_fragments(md) == [
        "enabling-the-node-authentication-and-authorization-mode"
    ]


def test_pr_51711_en_link_target_blocks_ru_translit_fragment():
    """#51711: href-only EN keep RU translit → hard fail with available anchors."""
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    href = (
        "../../devops/concepts/node-authorization.md"
        "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    )
    files = {
        target: "## Enabling the node authentication and authorization mode\n",
    }
    en = f"This option is advisable to use when [registering dynamic nodes]({href}).\n"
    msgs = check_en_page_link_targets(page, en, read_text=files.get)
    assert len(msgs) == 1
    assert msgs[0].startswith("en_link_target: client_certificate_authorization.md:")
    assert "missing fragment: vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov" in msgs[0]
    assert "available: enabling-the-node-authentication-and-authorization-mode" in msgs[0]


def test_en_link_target_ignores_yfm_include_directives():
    """§6.230 / #37673: `{% include [overlay](…md) %}` is not a Markdown link."""
    page = "ydb/docs/en/core/recipes/ydb-sdk/debug-logs.md"
    en = (
        "Go:\n\n"
        "{% list tabs %}\n\n"
        "- Go\n\n"
        "  {% include [overlay](_includes/debug-logs-go-appendix.md) %}\n\n"
        "{% endlist %}\n"
    )
    files = {
        # Empty tip stub — still a present file.
        "ydb/docs/en/core/recipes/ydb-sdk/_includes/debug-logs-go-appendix.md": "",
    }
    assert check_en_page_link_targets(page, en, read_text=files.get) == []


def test_en_link_target_empty_file_is_present_not_missing():
    page = "ydb/docs/en/core/a.md"
    en = "See [stub](./b.md).\n"
    files = {"ydb/docs/en/core/b.md": ""}
    assert check_en_page_link_targets(page, en, read_text=files.get) == []
    assert check_en_page_link_targets(page, en, read_text=lambda _p: None) != []


def test_en_link_target_suppresses_ambient_baseline_debt():
    """§6.228: tip-main link debt does not block; newly introduced debt does."""
    page = "ydb/docs/en/core/security/authentication.md"
    auth = "ydb/docs/en/core/reference/configuration/auth_config.md"
    mon = "ydb/docs/en/core/reference/configuration/monitoring_config.md"
    files = {
        auth: "## Local auth {#local-auth-config}\n",
        mon: "## Authentication {#authentication}\n",
    }
    baseline = (
        "See [lockout](../reference/configuration/auth_config.md#account-lockout).\n"
    )
    # Same ambient debt + new broken #tls from this PR's RU delta.
    current = (
        baseline
        + "See [tls](../reference/configuration/monitoring_config.md#tls).\n"
    )
    msgs = check_en_page_link_targets(
        page, current, read_text=files.get, baseline_text=baseline
    )
    assert len(msgs) == 1
    assert "missing fragment: tls" in msgs[0]
    assert "account-lockout" not in msgs[0]


def test_apply_en_link_target_checks_blocks_href_only_pair(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "ydb/docs/en/core/devops/concepts/node-authorization.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "## Enabling the node authentication and authorization mode\n",
        encoding="utf-8",
    )
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    pair = DocPair(
        ru_path=page.replace("/docs/en/", "/docs/ru/", 1),
        en_path=page,
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=page,
        source_lang="ru",
        target_lang="en",
        summary="href-only",
    )
    href = (
        "../../devops/concepts/node-authorization.md"
        "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    )
    text = f"See [nodes]({href}).\n"
    # Href-only path: target_text set, file_result absent (no pair heuristics).
    result = PRTranslationResult(pair_results=[PairRunResult(plan=plan, target_text=text)])
    broken = apply_en_link_target_checks(result, repo_path=str(repo), en_md_paths={page})
    assert broken == [page]
    fr = result.pair_results[0].file_result
    assert fr is not None
    assert fr.verdict == "blocked"
    assert any(m.startswith("en_link_target:") for m in fr.heuristic_blocking)


def test_apply_en_link_target_checks_prefers_post_repair_disk_text(tmp_path):
    """§6.227: late disk repair wins over stale PairRunResult target_text."""
    repo = tmp_path / "repo"
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    target_file = repo / target
    target_file.parent.mkdir(parents=True)
    target_file.write_text("## Node authorization {#node-authorization}\n", encoding="utf-8")
    page_file = repo / page
    page_file.parent.mkdir(parents=True)
    page_file.write_text(
        "See [nodes](../../devops/concepts/node-authorization.md#node-authorization).\n",
        encoding="utf-8",
    )
    pair = DocPair(
        ru_path=page.replace("/docs/en/", "/docs/ru/", 1),
        en_path=page,
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=page,
        source_lang="ru",
        target_lang="en",
        summary="href-only",
    )
    stale = (
        "See [nodes](../../devops/concepts/node-authorization.md"
        "#vklyuchenie-rezhima-avtorizacii).\n"
    )
    result = PRTranslationResult(pair_results=[PairRunResult(plan=plan, target_text=stale)])

    assert apply_en_link_target_checks(result, repo_path=str(repo), en_md_paths={page}) == []
    assert result.pair_results[0].file_result is None
