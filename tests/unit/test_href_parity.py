"""§6.174: RU↔EN href / anchor parity and inbound fragment checks."""

from __future__ import annotations

from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.href_parity import (
    check_heading_anchor_parity,
    check_href_parity,
    check_inbound_fragments,
    collect_internal_hrefs,
)


def test_href_parity_requires_exact_same_links():
    ru = (
        "See [{#T}](../../../security/authentication.md#ldap) and "
        "[SID](../../../concepts/glossary.md#access-sid).\n"
    )
    en_ok = (
        "See [{#T}](../../../security/authentication.md#ldap) and "
        "[SID](../../../concepts/glossary.md#access-sid).\n"
    )
    en_bad = (
        "See [{#T}](../../../security/authentication.md#ldap-auth-provider) and "
        "[SID](../../../concepts/glossary.md#access-sid).\n"
    )
    assert check_href_parity(ru, en_ok) == []
    msgs = check_href_parity(ru, en_bad)
    assert len(msgs) == 1
    assert msgs[0].startswith("href_parity:")
    assert "ldap-auth-provider" in msgs[0]
    assert "authentication.md#ldap" in msgs[0]


def test_href_parity_flags_extra_en_link():
    ru = "See [{#T}](foo.md#a).\n"
    en = "See [{#T}](foo.md#a) and [more](bar.md#b).\n"
    msgs = check_href_parity(ru, en)
    assert msgs and "extra in EN" in msgs[0]
    assert "bar.md#b" in msgs[0]


def test_anchor_parity_blocks_renamed_heading_id():
    ru = "## LDAP {#ldap}\n\n### TLS {#ldap-tls}\n"
    en = "## LDAP {#ldap-auth-provider}\n\n### TLS {#ldap-tls}\n"
    msgs = check_heading_anchor_parity(ru, en)
    assert msgs and msgs[0].startswith("anchor_parity:")
    assert "ldap" in msgs[0]
    assert "ldap-auth-provider" in msgs[0]


def test_inbound_fragment_catches_48792_hole():
    """Translating auth to {#ldap} while classifier still has #ldap-auth-provider."""
    auth_path = "ydb/docs/en/core/security/authentication.md"
    auth_en = "## Authentication using LDAP directory {#ldap}\n"
    files = {
        auth_path: "## old {#ldap-auth-provider}\n",  # disk stale; in-flight text wins
        "ydb/docs/en/core/yql/reference/syntax/create-resource-pool-classifier.md": (
            "See [{#T}](../../../security/authentication.md#ldap-auth-provider).\n"
        ),
        "ydb/docs/en/core/security/index.md": "See [{#T}](authentication.md#ldap).\n",
    }
    msgs = check_inbound_fragments(
        auth_path,
        auth_en,
        en_paths=list(files),
        read_text=files.get,
    )
    assert any(m.startswith("inbound_fragment:") for m in msgs)
    assert any("ldap-auth-provider" in m for m in msgs)
    assert not any("authentication.md#ldap`" in m for m in msgs)


def test_heuristics_classify_href_parity_blocking():
    ru = "See [{#T}](authentication.md#ldap).\n"
    en = "See [{#T}](authentication.md#ldap-auth-provider).\n"
    classified = run_file_heuristics_classified(
        ru,
        en,
        normalized_source_text=ru,
        source_lang="ru",
        target_lang="en",
        source_file="ydb/docs/ru/core/security/authentication.md",
    )
    assert any(m.startswith("href_parity:") for m in classified.blocking)


def test_collect_skips_http():
    text = "[wiki](https://en.wikipedia.org/wiki/LDAP) and [{#T}](a.md#b).\n"
    assert collect_internal_hrefs(text) == ["a.md#b"]
