"""Tests for EN fragment repair (§6.142 / #48047)."""

from __future__ import annotations

from ydbdoc_review.validation.fragment_repair import (
    fragment_declared_in_markdown,
    repair_en_fragments,
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


def test_pr_50976_sid_fragment_localizes_to_declared_en_fragment():
    en_page = "ydb/docs/en/core/security/index.md"
    ru_source = "См. [SID](./authorization.md#sid).\n"
    en_exact = "See [SID](./authorization.md#sid).\n"
    files = {
        "ydb/docs/en/core/security/authorization.md": "## User {#user}\n",
        "ydb/docs/ru/core/security/authorization.md": "## SID {#sid}\n",
    }

    assert (
        repair_en_fragments(
            en_exact,
            en_page_path=en_page,
            read_text=files.get,
            ru_source=ru_source,
        )
        == "See [SID](./authorization.md#user).\n"
    )


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
