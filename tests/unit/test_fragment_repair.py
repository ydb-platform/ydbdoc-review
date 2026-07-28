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
    en_bad = (
        "Sessions are described in "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
    en_baseline = (
        "Sessions are described in "
        "[{#T}](query_execution/execution_process.md#sessions).\n"
    )
    ru_stale = (
        "Сессии описаны в "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
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
    en_bad = (
        "Sessions are described in "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
    ru_ok = (
        "Сессии описаны в "
        "[{#T}](query_execution/execution_process.md#sessions).\n"
    )
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


def test_pr_48047_ldap_remaps_via_heading_map():
    """RU {#ldap} vs EN {#ldap-auth-provider} on authentication twin."""
    en_page = (
        "ydb/docs/en/core/yql/reference/syntax/create-resource-pool-classifier.md"
    )
    en_text = (
        "For more information, see "
        "[{#T}](../../../security/authentication.md#ldap).\n"
    )
    files = {
        "ydb/docs/en/core/security/authentication.md": (
            "## LDAP directory integration {#ldap-auth-provider}\n\n"
            "### TLS {#ldap-tls}\n"
        ),
        "ydb/docs/ru/core/security/authentication.md": (
            "## Аутентификация с использованием LDAP-каталога {#ldap}\n\n"
            "### TLS {#ldap-tls}\n"
        ),
    }
    fixed = repair_en_fragments(
        en_text,
        en_page_path=en_page,
        read_text=files.get,
    )
    assert "authentication.md#ldap-auth-provider" in fixed
    assert "authentication.md#ldap." not in fixed
    assert "authentication.md#ldap)" not in fixed


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


def test_pr_48012_sessions_finds_sibling_when_ru_and_en_baseline_stale():
    """§6.153 / #48012: both RU and EN still say index.md#sessions — use toc sibling."""
    en_page = "ydb/docs/en/core/concepts/glossary.md"
    stale = (
        "Sessions are described in "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
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
