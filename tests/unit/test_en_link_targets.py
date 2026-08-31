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


def test_en_link_target_ok_when_fragment_matches():
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    href = (
        "../../devops/concepts/node-authorization.md"
        "#enabling-the-node-authentication-and-authorization-mode"
    )
    files = {
        target: "## Enabling the node authentication and authorization mode\n",
    }
    en = f"See [nodes]({href}).\n"
    assert check_en_page_link_targets(page, en, read_text=files.get) == []


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
