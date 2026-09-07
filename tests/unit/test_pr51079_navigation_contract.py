"""Regression contracts for PR 51079 navigation recovery."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest
import yaml

from ydbdoc_review.config.loader import RuAuthorityMode, load_config
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.navigation.toc import merge_en_toc_yaml
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.navigation_merge import merge_navigation_pair
from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.toc_targets import apply_orphan_toc_page_checks


@pytest.mark.parametrize("parent_scoped", [False, True])
def test_scoped_caching_child_survives_href_parent(parent_scoped: bool) -> None:
    ru = (
        "items:\n"
        "- name: Auth RU\n"
        "  href: authentication.md\n"
        "  items:\n"
        "  - name: Cache RU\n"
        "    href: caching-authentication-results.md\n"
    )
    en = "items:\n- name: Authentication\n  href: authentication.md\n"
    scope = {"caching-authentication-results.md"}
    if parent_scoped:
        scope.add("authentication.md")

    merged = merge_en_toc_yaml(
        en,
        ru,
        translate_hrefs=scope,
        translate_name=lambda name: {
            "Auth RU": "Authentication",
            "Cache RU": "Caching",
        }[name],
        restrict_gap_fill_to_scope=True,
    )

    parent = yaml.safe_load(merged)["items"][0]
    assert parent["href"] == "authentication.md"
    assert parent["name"] == "Authentication"
    assert parent["items"][0]["href"] == "caching-authentication-results.md"


def test_reordered_href_parents_match_en_identity_and_keep_nested_scope() -> None:
    en = dedent("""
        items:
        - name: Alpha EN
          href: alpha.md
          when: feature_alpha
          items:
          - name: Alpha stable
            href: alpha-stable.md
        - name: Beta EN
          href: beta.md
          when: feature_beta
          items:
          - name: Beta stable
            href: beta-stable.md
        - name: EN only
          href: en-only.md
    """).strip()
    ru = dedent("""
        items:
        - name: Бета
          href: beta.md
          items:
          - name: Бета стабильная
            href: beta-stable.md
          - name: Бета новая
            href: beta-new.md
        - name: Альфа
          href: alpha.md
          items:
          - name: Альфа стабильная
            href: alpha-stable.md
          - name: Альфа вне области
            href: alpha-out-of-scope.md
    """).strip()

    merged = merge_en_toc_yaml(
        en,
        ru,
        translate_hrefs={"beta-new.md"},
        translate_name=lambda name: {"Бета новая": "Beta new"}[name],
        ru_base_hrefs={
            "alpha.md",
            "alpha-stable.md",
            "beta.md",
            "beta-stable.md",
            "removed-child.md",
        },
        restrict_gap_fill_to_scope=True,
    )
    items = yaml.safe_load(merged)["items"]

    assert [(item["name"], item.get("href")) for item in items[:2]] == [
        ("Beta EN", "beta.md"),
        ("Alpha EN", "alpha.md"),
    ]
    assert items[0]["when"] == "feature_beta"
    assert items[1]["when"] == "feature_alpha"
    assert [child["href"] for child in items[0]["items"]] == [
        "beta-stable.md",
        "beta-new.md",
    ]
    assert [child["href"] for child in items[1]["items"]] == ["alpha-stable.md"]
    assert "alpha-out-of-scope.md" not in merged
    assert "en-only.md" in merged


def test_scoped_mixed_parent_keeps_ru_href_include_and_when_metadata() -> None:
    ru = dedent("""
        items:
        - name: Аутентификация
          href: authentication.md
          when: feature_auth
          include:
            mode: link
            path: authentication/toc_p.yaml
          items:
          - name: Кеширование
            href: caching-authentication-results.md
    """).strip()
    en = dedent("""
        items:
        - name: Authentication
          href: authentication.md
    """).strip()

    merged = merge_en_toc_yaml(
        en,
        ru,
        translate_hrefs={
            "authentication.md",
            "caching-authentication-results.md",
        },
        translate_name=lambda name: {
            "Аутентификация": "Authentication",
            "Кеширование": "Caching",
        }[name],
        restrict_gap_fill_to_scope=True,
    )
    parent = yaml.safe_load(merged)["items"][0]

    assert parent["href"] == "authentication.md"
    assert parent["when"] == "feature_auth"
    assert parent["include"] == {
        "mode": "link",
        "path": "authentication/toc_p.yaml",
    }
    assert parent["items"][0]["href"] == "caching-authentication-results.md"


def test_explicitly_removed_nested_child_is_not_retained() -> None:
    en = dedent("""
        items:
        - name: Authentication
          href: authentication.md
          items:
          - name: Stable
            href: stable.md
          - name: Removed
            href: removed-child.md
    """).strip()
    ru = dedent("""
        items:
        - name: Аутентификация
          href: authentication.md
          items:
          - name: Стабильная
            href: stable.md
    """).strip()

    merged = merge_en_toc_yaml(
        en,
        ru,
        translate_hrefs=set(),
        translate_name=lambda name: name,
        ru_base_hrefs={"authentication.md", "stable.md", "removed-child.md"},
        restrict_gap_fill_to_scope=True,
    )

    assert "stable.md" in merged
    assert "removed-child.md" not in merged


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _translated_run(en_path: str, text: str) -> PairRunResult:
    pair = DocPair(
        ru_path=en_path.replace("/en/", "/ru/", 1),
        en_path=en_path,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=en_path,
        source_lang="ru",
        target_lang="en",
    )
    file_result = FileTranslationResult(
        file_path=en_path,
        final_text=text,
        segments_count=1,
        verdict="ok",
        prompt_version="test",
    )
    return PairRunResult(
        plan=plan,
        target_text=text,
        file_result=file_result,
    )


def _pr51079_repo(tmp_path: Path) -> tuple[Path, RuAuthority, NavigationPair]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    ru_sidebar = "ydb/docs/ru/core/security/toc_p.yaml"
    en_sidebar = "ydb/docs/en/core/security/toc_p.yaml"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        dedent("""
            items:
            - name: Security
              include:
                mode: link
                path: security/toc_p.yaml
            - name: Configuration
              href: reference/configuration/auth_config.md
            - name: Glossary
              href: concepts/glossary.md
        """).strip()
        + "\n",
    )
    flat_sidebar = "items:\n- name: Authentication\n  href: authentication.md\n"
    _write(repo, ru_sidebar, flat_sidebar)
    _write(repo, en_sidebar, flat_sidebar)
    _write(repo, "ydb/docs/en/core/security/authentication.md", "# Authentication\n")
    _write(
        repo,
        "ydb/docs/en/core/reference/configuration/auth_config.md",
        "# Authentication configuration\n",
    )
    _write(repo, "ydb/docs/en/core/concepts/glossary.md", "# Glossary\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "frozen baseline")
    base_sha = _git(repo, "rev-parse", "HEAD")

    _write(
        repo,
        ru_sidebar,
        (
            "items:\n"
            "- name: Аутентификация\n"
            "  href: authentication.md\n"
            "  items:\n"
            "  - name: Кеширование результатов аутентификации\n"
            "    href: caching-authentication-results.md\n"
        ),
    )
    _git(repo, "add", ru_sidebar)
    _git(repo, "commit", "-m", "source PR navigation")
    head_sha = _git(repo, "rev-parse", "HEAD")
    authority = RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=51079,
        source_base_sha=base_sha,
        source_head_sha=head_sha,
        baseline_sha=base_sha,
        ru_sha=head_sha,
        mode=RuAuthorityMode.SOURCE_PRESERVING,
    )
    pair = NavigationPair(
        ru_path=ru_sidebar,
        en_path=en_sidebar,
        ru_changed=True,
    )
    return repo, authority, pair


def _six_pr51079_runs() -> list[PairRunResult]:
    security = "ydb/docs/en/core/security"
    return [
        _translated_run(
            "ydb/docs/en/core/reference/configuration/auth_config.md",
            "# Authentication configuration\n",
        ),
        _translated_run(f"{security}/authentication.md", "# Authentication\n"),
        _translated_run("ydb/docs/en/core/concepts/glossary.md", "# Glossary\n"),
        _translated_run(
            f"{security}/caching-authentication-results.md",
            "# Caching authentication results\n\n"
            "{% include [User token](_assets/user-token.md) %}\n\n"
            "{% include [Lifecycle](_assets/user-token-lifecycle.md) %}\n",
        ),
        _translated_run(f"{security}/_assets/user-token.md", "User token.\n"),
        _translated_run(
            f"{security}/_assets/user-token-lifecycle.md",
            "User token lifecycle.\n",
        ),
    ]


def test_pr51079_navigation_merge_proves_caching_and_include_reachability(
    tmp_path: Path,
) -> None:
    repo, authority, pair = _pr51079_repo(tmp_path)
    config = load_config(env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"})
    with patch(
        "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
        return_value={"Кеширование результатов аутентификации": ("Caching authentication results")},
    ):
        navigation = merge_navigation_pair(
            pair,
            repo_path=str(repo),
            merge_base_with=authority.baseline_sha,
            client=MagicMock(),
            glossary=load_glossary(),
            config=config,
            authority=authority,
        )

    assert navigation.target_text is not None
    parent = yaml.safe_load(navigation.target_text)["items"][0]
    assert parent["href"] == "authentication.md"
    assert parent["items"][0]["href"] == "caching-authentication-results.md"

    result = PRTranslationResult(
        pair_results=_six_pr51079_runs(),
        navigation_results=[navigation],
    )
    assert (
        apply_orphan_toc_page_checks(
            result,
            repo_path=str(repo),
            baseline_ref=authority.baseline_sha,
        )
        == []
    )

    without_caching = NavigationRunResult(
        ru_path=navigation.ru_path,
        en_path=navigation.en_path,
        kind=navigation.kind,
        target_text=("items:\n- name: Authentication\n  href: authentication.md\n"),
        verdict="ok",
    )
    control = PRTranslationResult(
        pair_results=_six_pr51079_runs(),
        navigation_results=[without_caching],
    )
    assert apply_orphan_toc_page_checks(
        control,
        repo_path=str(repo),
        baseline_ref=authority.baseline_sha,
    ) == [
        "ydb/docs/en/core/security/_assets/user-token-lifecycle.md",
        "ydb/docs/en/core/security/_assets/user-token.md",
        "ydb/docs/en/core/security/caching-authentication-results.md",
    ]
