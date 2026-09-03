"""Tests for EN fragment repair (§6.142 / #48047)."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.validation.en_link_targets import apply_en_link_target_checks
from ydbdoc_review.validation.fragment_repair import (
    _page_declares_fragment,
    add_explicit_ascii_fragment_anchor,
    fragment_declared_in_markdown,
    repair_en_fragments,
)

_LEGACY_NODE_AUTH_FRAG = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"


def test_real_tip_connect_without_tls_heading_cannot_receive_anchor():
    ru = "# CLI\n\n## Connect\n\n### TLS {#tls}\n\n### Other\n"
    en = "# CLI\n\n## Connect\n\n### Other\n"
    before = en
    assert add_explicit_ascii_fragment_anchor(en, ru, "tls") is None
    assert en == before
    assert "{#tls}" not in en


def test_synthetic_aligned_heading_gets_exact_ascii_anchor_append_only():
    ru = "# CLI\n\n## Connect\n\n### TLS {#tls}\n\n### Other\n"
    en = "# CLI\n\n## Connect\n\n### TLS connection parameters\n\n### Other\n"
    assert add_explicit_ascii_fragment_anchor(en, ru, "tls") == (
        "# CLI\n\n## Connect\n\n### TLS connection parameters {#tls}\n\n### Other\n"
    )


def test_fragment_declared_in_markdown():
    assert fragment_declared_in_markdown("## Sessions {#sessions}\n", "sessions")
    assert not fragment_declared_in_markdown("## Sessions {#sessions}\n", "ldap")


def test_pr_48047_sessions_prefers_en_baseline_path():
    """Stale RU/force_exact left index.md#sessions; EN baseline + target page win."""
    en_page = "ydb/docs/en/core/concepts/glossary.md"
    en_bad = "Sessions are described in [{#T}](query_execution/index.md#sessions).\n"
    en_baseline = (
        "Sessions are described in [{#T}](query_execution/execution_process.md#sessions).\n"
    )
    ru_stale = "Сессии описаны в [{#T}](query_execution/index.md#sessions).\n"
    files = {
        "ydb/docs/en/core/concepts/query_execution/index.md": (
            "# Query execution\n\n## Tables {#tables}\n"
        ),
        "ydb/docs/en/core/concepts/query_execution/execution_process.md": (
            "# Process\n\n## Sessions {#sessions}\n"
        ),
    }
    fixed = repair_en_fragments(
        en_bad,
        en_page_path=en_page,
        read_text=files.get,
        ru_source=ru_stale,
        en_baseline=en_baseline,
    )
    assert "execution_process.md#sessions" in fixed
    assert "index.md#sessions" not in fixed


def test_pr_48047_sessions_uses_ru_overlay_path_when_en_declares():
    """RU source already points at execution_process; retarget EN."""
    en_page = "ydb/docs/en/core/concepts/glossary.md"
    en_bad = "Sessions are described in [{#T}](query_execution/index.md#sessions).\n"
    ru_ok = "Сессии описаны в [{#T}](query_execution/execution_process.md#sessions).\n"
    files = {
        "ydb/docs/en/core/concepts/query_execution/index.md": (
            "# Query execution\n\n## Tables {#tables}\n"
        ),
        "ydb/docs/en/core/concepts/query_execution/execution_process.md": (
            "# Process\n\n## Sessions {#sessions}\n"
        ),
    }
    fixed = repair_en_fragments(
        en_bad,
        en_page_path=en_page,
        read_text=files.get,
        ru_source=ru_ok,
        en_baseline=en_bad,
    )
    assert "execution_process.md#sessions" in fixed
    assert "index.md#sessions" not in fixed


def test_pr_48047_ldap_does_not_remap_to_en_only_fragment():
    """§6.174: keep RU ``#ldap``; do not invent ``#ldap-auth-provider``."""
    en_page = "ydb/docs/en/core/yql/reference/syntax/create-resource-pool-classifier.md"
    en_text = "For more information, see [{#T}](../../../security/authentication.md#ldap).\n"
    files = {
        "ydb/docs/en/core/security/authentication.md": (
            "## LDAP directory integration {#ldap-auth-provider}\n\n### TLS {#ldap-tls}\n"
        ),
        "ydb/docs/ru/core/security/authentication.md": (
            "## Аутентификация с использованием LDAP-каталога {#ldap}\n\n### TLS {#ldap-tls}\n"
        ),
    }
    fixed = repair_en_fragments(
        en_text,
        en_page_path=en_page,
        read_text=files.get,
    )
    assert "authentication.md#ldap)" in fixed or "authentication.md#ldap\n" in fixed
    assert "ldap-auth-provider" not in fixed


def test_repair_keeps_valid_fragment():
    en_page = "ydb/docs/en/core/concepts/glossary.md"
    en_ok = "See [{#T}](query_execution/execution_process.md#sessions).\n"
    files = {
        "ydb/docs/en/core/concepts/query_execution/execution_process.md": (
            "## Sessions {#sessions}\n"
        ),
    }
    fixed = repair_en_fragments(
        en_ok,
        en_page_path=en_page,
        read_text=files.get,
    )
    assert fixed == en_ok


def test_pr_45949_client_cert_legacy_translit_fragment():
    """TASK-51797: RU ASCII translit remains exact; target is declared later."""
    en_page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    frag = _LEGACY_NODE_AUTH_FRAG
    en_bad = (
        f"when [registering dynamic nodes](../../devops/concepts/node-authorization.md#{frag}).\n"
    )
    files = {
        "ydb/docs/en/core/devops/concepts/node-authorization.md": (
            "## Enabling node authentication and authorization\n\nBody.\n"
        ),
        "ydb/docs/ru/core/devops/concepts/node-authorization.md": (
            "## Включение режима аутентификации и авторизации узлов\n\nТело.\n"
        ),
    }
    # Pair repair must not invent an EN-only slug.
    fixed = repair_en_fragments(
        en_bad,
        en_page_path=en_page,
        read_text=files.get,
    )
    assert fixed == en_bad


def test_page_declares_fragment_accepts_legacy_translit_slug():
    """R-GL-1: bare RU heading owns its legacy ASCII translit id."""
    ru = "## Включение режима аутентификации и авторизации узлов\n\nТело.\n"
    assert _page_declares_fragment(ru, _LEGACY_NODE_AUTH_FRAG)
    assert not _page_declares_fragment(
        "## Enabling the node authentication and authorization mode\n",
        _LEGACY_NODE_AUTH_FRAG,
    )


def test_pr_40385_legacy_translit_declare_writes_exact_ascii_and_clears_gate(tmp_path: Path):
    """#40385 / R-GL-1: declare finds legacy RU owner and anchors EN target."""
    from ydbdoc_review.github.workflow import _declare_exact_ascii_fragment_targets_after_apply

    frag = _LEGACY_NODE_AUTH_FRAG
    repo = tmp_path / "repo"
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    en_target = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    ru_target = en_target.replace("/docs/en/", "/docs/ru/", 1)
    href = f"../../devops/concepts/node-authorization.md#{frag}"
    page_text = f"when [registering dynamic nodes]({href}).\n"
    en_heading = "## Enabling the node authentication and authorization mode\n\nBody.\n"
    ru_heading = "## Включение режима аутентификации и авторизации узлов\n\nТело.\n"

    for rel, text in (
        (page, page_text),
        (en_target, en_heading),
        (ru_target, ru_heading),
        (page.replace("/docs/en/", "/docs/ru/", 1), f"когда [узлы]({href}).\n"),
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    assert not _page_declares_fragment(en_heading, frag)
    assert _page_declares_fragment(ru_heading, frag)

    declared = _declare_exact_ascii_fragment_targets_after_apply(
        str(repo), [page], dry_run=False
    )
    assert declared == [en_target]
    en_after = (repo / en_target).read_text(encoding="utf-8")
    assert f"{{#{frag}}}" in en_after
    assert "enabling-the-node-authentication-and-authorization-mode" not in en_after

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
        summary="legacy-translit-declare",
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text=page_text)]
    )
    assert apply_en_link_target_checks(
        result, repo_path=str(repo), en_md_paths={page, en_target}
    ) == []


def test_pr_40385_redirect_from_path_uses_live_ru_twin_and_existing_en_target():
    """§6.227: redirect from-path EN pairs with RU at to-path."""
    en_page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    fragment = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    manual_href = f"../../devops/deployment-options/manual/node-authorization.md#{fragment}"
    en_manual = "## Enabling database node authentication and authorization\n"
    ru_concepts = "## Включение режима аутентификации и авторизации узлов\n"
    redirects = (
        "common:\n"
        "  - from: /devops/deployment-options/manual/node-authorization.md\n"
        "    to: /devops/concepts/node-authorization.md\n"
    )
    base_files = {
        "ydb/docs/redirects.yaml": redirects,
        "ydb/docs/en/core/devops/deployment-options/manual/node-authorization.md": en_manual,
        "ydb/docs/ru/core/devops/concepts/node-authorization.md": ru_concepts,
    }

    fixed_on_manual = repair_en_fragments(
        f"See [node authorization]({manual_href}).\n",
        en_page_path=en_page,
        read_text=base_files.get,
    )
    assert fixed_on_manual == f"See [node authorization]({manual_href}).\n"

    files_with_live_en = {
        **base_files,
        "ydb/docs/en/core/devops/concepts/node-authorization.md": en_manual,
    }
    fixed_on_live_path = repair_en_fragments(
        f"See [node authorization]({manual_href}).\n",
        en_page_path=en_page,
        read_text=files_with_live_en.get,
    )
    assert fixed_on_live_path == f"See [node authorization]({manual_href}).\n"


def test_pr_40385_system_views_users_fragment():
    """§6.221: RU autogen slug in link → EN explicit ``{#users}``."""
    en_page = "ydb/docs/en/core/security/authentication.md"
    en_bad = "See the [system view](../dev/system-views.md#информация-о-пользователях-users).\n"
    files = {
        "ydb/docs/en/core/dev/system-views.md": ("### Information about users {#users}\n\nBody.\n"),
        "ydb/docs/ru/core/dev/system-views.md": (
            "### Информация о пользователях {#users}\n\nТело.\n"
        ),
    }
    fixed = repair_en_fragments(
        en_bad,
        en_page_path=en_page,
        read_text=files.get,
    )
    assert "system-views.md#users)" in fixed
    assert "информация-о-пользователях" not in fixed


def test_pr_40385_system_views_localizes_to_declared_en_fragment():
    en_page = "ydb/docs/en/core/security/authentication.md"
    ru_source = (
        "См. [системного представления](../dev/system-views.md#информация-о-пользователях-users).\n"
    )
    en_bad = "See the [system view](../dev/system-views.md#информация-о-пользователях-users).\n"
    files = {
        "ydb/docs/en/core/dev/system-views.md": ("### Information about users {#users}\n\nBody.\n"),
        "ydb/docs/ru/core/dev/system-views.md": (
            "### Информация о пользователях {#users}\n\nТело.\n"
        ),
    }
    fixed = repair_en_fragments(
        en_bad,
        en_page_path=en_page,
        read_text=files.get,
        ru_source=ru_source,
    )
    assert "system-views.md#users)" in fixed


def test_pr_40385_prefers_valid_en_baseline_href_after_ru_restore():
    """§6.227: do not keep a restored RU fragment absent from the EN target."""
    en_page = "ydb/docs/en/core/security/authentication.md"
    restored = (
        "Configure [authentication](../reference/configuration/auth_config.md#security-auth).\n"
    )
    baseline = (
        "Configure [authentication](../reference/configuration/auth_config.md#authentication).\n"
    )
    files = {
        "ydb/docs/en/core/reference/configuration/auth_config.md": (
            "## Authentication {#authentication}\n"
        ),
    }

    fixed = repair_en_fragments(
        restored,
        en_page_path=en_page,
        read_text=files.get,
        en_baseline=baseline,
    )

    assert fixed == baseline


def test_pr_50976_ascii_explicit_fragment_is_not_localized():
    en_page = "ydb/docs/en/core/security/index.md"
    ru_source = "См. [SID](./authorization.md#sid).\n"
    en_exact = "See [SID](./authorization.md#sid).\n"
    files = {
        "ydb/docs/en/core/security/authorization.md": "## User {#user}\n",
        "ydb/docs/ru/core/security/authorization.md": "## SID {#sid}\n",
    }

    assert repair_en_fragments(
                en_exact,
                en_page_path=en_page,
                read_text=files.get,
                ru_source=ru_source,
            ) == en_exact


def test_pr_48223_does_not_mangle_existing_targets_to_bare_basenames():
    """§6.158 / #48223: existing table.md / classifier.md must not become
    unreachable ``topic.md`` / ``create-resource-pool.md`` under ``en/core/dev/``.
    """
    en_page = "ydb/docs/en/core/dev/system-views.md"
    good = (
        "about [partitions](../concepts/datamodel/table.md#partitioning) of tables.\n"
        "about [settings](../yql/reference/syntax/create-resource-pool-classifier.md"
        "#parameters) of classifiers.\n"
    )
    files = {
        "ydb/docs/en/core/concepts/datamodel/table.md": (
            "{% include [table.md](_includes/table.md) %}\n"
        ),
        "ydb/docs/en/core/concepts/datamodel/_includes/table.md": (
            "### Partitioning Row-Oriented Tables {#partitioning_row_table}\n"
        ),
        "ydb/docs/en/core/concepts/datamodel/topic.md": ("## Partitioning {#partitioning}\n"),
        "ydb/docs/en/core/concepts/datamodel/toc_i.yaml": (
            "items:\n- { name: Tables, href: table.md }\n- { name: Topics, href: topic.md }\n"
        ),
        "ydb/docs/en/core/yql/reference/syntax/create-resource-pool-classifier.md": (
            "### Parameters\n\nRank and pool.\n"
        ),
        "ydb/docs/en/core/yql/reference/syntax/create-resource-pool.md": (
            "### Parameters {#parameters}\n"
        ),
        "ydb/docs/en/core/yql/reference/syntax/toc_i.yaml": (
            "items:\n"
            "- { name: CREATE RESOURCE POOL, href: create-resource-pool.md }\n"
            "- { name: CREATE RESOURCE POOL CLASSIFIER, "
            "href: create-resource-pool-classifier.md }\n"
        ),
    }
    fixed = repair_en_fragments(
        good,
        en_page_path=en_page,
        read_text=files.get,
    )
    # Existing targets must stay under datamodel / yql paths — not bare basenames.
    assert "topic.md#partitioning" not in fixed
    assert "](create-resource-pool.md#parameters)" not in fixed
    assert "](topic.md#" not in fixed
    # Classifier keeps path; Parameters auto-slug counts as declared.
    assert "create-resource-pool-classifier.md#parameters" in fixed
    # §6.174: do not invent EN-only ``#partitioning_row_table`` — keep RU frag.
    assert "table.md#partitioning)" in fixed or "table.md#partitioning\n" in fixed
    assert "partitioning_row_table" not in fixed


def test_fragment_declared_accepts_diplodoc_auto_slug():
    assert fragment_declared_in_markdown("### Parameters\n\nbody\n", "parameters")


def test_pr_48012_sessions_finds_sibling_when_ru_and_en_baseline_stale():
    """§6.153 / #48012: both RU and EN still say index.md#sessions — use toc sibling."""
    en_page = "ydb/docs/en/core/concepts/glossary.md"
    stale = "Sessions are described in [{#T}](query_execution/index.md#sessions).\n"
    files = {
        "ydb/docs/en/core/concepts/query_execution/index.md": (
            "# Query execution\n\nSee [{#T}](execution_process.md).\n"
        ),
        "ydb/docs/en/core/concepts/query_execution/execution_process.md": (
            "# Process\n\n## Sessions {#sessions}\n"
        ),
        "ydb/docs/en/core/concepts/query_execution/toc_i.yaml": (
            "items:\n"
            "- { name: Overview, href: index.md }\n"
            "- { name: Process, href: execution_process.md }\n"
        ),
    }
    fixed = repair_en_fragments(
        stale,
        en_page_path=en_page,
        read_text=files.get,
        ru_source=stale.replace("Sessions are", "Сессии"),
        en_baseline=stale,
    )
    assert "execution_process.md#sessions" in fixed
    assert "index.md#sessions" not in fixed
