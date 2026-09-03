"""§6.174: RU↔EN href / anchor parity and inbound fragment checks."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.href_parity import (
    apply_href_only_delta,
    apply_localized_mirror_delta,
    check_heading_anchor_parity,
    check_href_parity,
    check_inbound_fragments,
    check_outbound_fragments,
    collect_internal_hrefs,
    is_href_only_change,
    is_localized_mirror_delta,
    restore_md_link_hrefs,
)


def test_apply_href_only_delta_preserves_target_bytes_except_changed_href():
    ru_base = "Before. [Авторизация](../manual/node.md). After. [CMS](cms.md).\n"
    ru_now = "Before. [Авторизация](../concepts/node.md). After. [CMS](cms.md).\n"
    en = "Before. [Authorization](../manual/node.md). After. [CMS](cms.md).\n"

    assert apply_href_only_delta(ru_base, ru_now, en) == (
        "Before. [Authorization](../concepts/node.md). After. [CMS](cms.md).\n"
    )


def test_apply_href_only_delta_rejects_ambiguous_target():
    ru_base = "[A](old.md)\n"
    ru_now = "[A](new.md)\n"
    en = "[One](old.md) and [Two](old.md)\n"

    assert apply_href_only_delta(ru_base, ru_now, en) is None
    assert is_href_only_change(ru_base, ru_now)


def test_pr_50904_href_parity_grandfathers_only_preexisting_baseline_gap():
    ru_base = "* [{#T}](../backup-and-recovery/index.md)\n"
    ru_now = ru_base + "* [{#T}](./node-authorization.md)\n"
    en_base = "* [{#T}](../backup-and-recovery.md)\n"
    en_now = en_base + "* [{#T}](./node-authorization.md)\n"

    assert check_href_parity(
        ru_now,
        en_now,
        source_baseline_text=ru_base,
        en_baseline_text=en_base,
    ) == []
    assert check_href_parity(
        ru_now,
        en_base,
        source_baseline_text=ru_base,
        en_baseline_text=en_base,
    )


def test_href_only_change_ignores_writer_trailing_blank_normalization():
    before = "See [node](old.md).\n\n"
    after = "See [node](new.md).\n"

    assert is_href_only_change(before, after)


def test_pr_50839_localized_mirror_delta_href_and_fence_info():
    ru_base = dedent(
        """\
        DecommitStatus | [Состояние](#decommit-progress).

        ```
        $ dstool list
        ```
        """
    )
    ru_now = dedent(
        """\
        DecommitStatus | [Состояние](blobdepot_decommit.md#decommit-progress).

        ```bash
        $ dstool list
        ```
        """
    )
    en = dedent(
        """\
        DecommitStatus | [State](blobdepot_decommit.md#decommit-progress).

        ```bash
        $ dstool list
        ```
        """
    )

    assert is_localized_mirror_delta(ru_base, ru_now)
    assert apply_localized_mirror_delta(ru_base, ru_now, en) == en


def test_pr_50839_autotitle_delta_already_in_en():
    from ydbdoc_review.translation.differential import autotitle_delta_satisfied_in_en

    ru_base = dedent(
        """\
        * Обслуживание:

          * [{#T}](moving_vdisks.md).
        """
    )
    ru_pr = ru_base + "  * [{#T}](blobdepot.md).\n  * [{#T}](blobdepot_decommit.md).\n"
    en = dedent(
        """\
        Main topics:

        * [{#T}](moving_vdisks.md).
        * [{#T}](blobdepot.md).
        * [{#T}](blobdepot_decommit.md).
        """
    )

    assert autotitle_delta_satisfied_in_en(ru_base, ru_pr, en)


def test_pr_50904_outbound_fragment_blocks_ru_slug_on_en_target():
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    href = "../../devops/concepts/node-authorization.md#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    files = {target: "## Enabling node authentication and authorization\n"}

    assert check_outbound_fragments(
        page,
        f"See [nodes]({href}).\n",
        read_text=files.get,
        en_baseline_text="See [nodes](../../devops/deployment-options/manual/node-authorization.md#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov).\n",
    ) == [
        "outbound_fragment: `"
        + href
        + "` points to missing EN anchor `node-authorization.md#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov`"
    ]


def test_outbound_fragment_blocks_missing_target():
    page = "ydb/docs/en/core/reference/client.md"
    href = "../missing.md#anchor"

    assert check_outbound_fragments(
        page,
        f"See [missing]({href}).\n",
        read_text=lambda _path: None,
    ) == ["outbound_fragment: `../missing.md#anchor` points to missing EN target `missing.md`"]


def test_restore_md_link_hrefs_applies_only_ru_delta():
    """#45949: one RU href edit must not permute unrelated EN links."""
    ru_base = "[DSL](selectors.md) [Kinds](#kinds) [Auth](../manual/auth.md) [CMS](glossary.md#cms)"
    ru_now = (
        "[DSL](selectors.md) [Kinds](#kinds) [Auth](../concepts/auth.md) [CMS](glossary.md#cms)"
    )
    en_base = ru_base

    fixed = restore_md_link_hrefs(
        en_base,
        ru_now,
        source_ru_base=ru_base,
        target_baseline=en_base,
    )

    assert fixed == (
        "[DSL](selectors.md) [Kinds](#kinds) [Auth](../concepts/auth.md) [CMS](glossary.md#cms)"
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


def test_href_parity_ignores_fenced_example_as_real_link():
    ru = "See [real](foo.md#ru).\n"
    en = "See real.\n\n```md\n[example](foo.md#en)\n```\n"
    assert check_href_parity(ru, en)


def test_href_parity_ignores_inline_code_and_images():
    ru = "See [real](foo.md#ru).\n"
    en = "Literal `[x](foo.md#ru)` and ![image](foo.md#ru).\n"
    assert check_href_parity(ru, en)


def test_href_parity_ignores_fenced_autotitle_example():
    ru = "See [{#T}](foo.md#ru).\n"
    en = "See real.\n\n```md\n[{#T}](foo.md#ru)\n```\n"
    assert check_href_parity(ru, en)


def test_href_parity_ignores_blockquoted_fence_and_indented_code():
    ru = "See [real](foo.md#x).\n"
    blockquote = "See real.\n\n> ```md\n> [example](foo.md#x)\n> ```\n"
    indented = "See real.\n\n    [example](foo.md#x)\n"
    assert check_href_parity(ru, blockquote)
    assert check_href_parity(ru, indented)


def test_href_parity_rejects_pr_50976_ascii_fragment_retargeting():
    page = "ydb/docs/en/core/security/index.md"
    ru = "[SID](./authorization.md#sid)\n"
    en = "[SID](./authorization.md#user)\n"
    files = {
        "ydb/docs/en/core/security/authorization.md": "## User {#user}\n",
    }
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        docs_text_reader=files.get,
        en_baseline_text=en,
    ) == [
        "href_parity: exact ASCII fragment changed: "
        "`./authorization.md#sid` -> `./authorization.md#user`"
    ]


def test_pr_51761_legacy_translit_may_map_to_proven_en_auto_slug():
    """Legacy RU heading slugs may localize to the aligned EN auto-slug."""
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target_en = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    target_ru = target_en.replace("/docs/en/", "/docs/ru/", 1)
    ru_frag = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    en_frag = "enabling-the-node-authentication-and-authorization-mode"
    href_ru = f"../../devops/concepts/node-authorization.md#{ru_frag}"
    href_en = f"../../devops/concepts/node-authorization.md#{en_frag}"
    ru = f"See [nodes]({href_ru}).\n"
    en = f"See [nodes]({href_en}).\n"
    files = {
        target_en: "## Enabling the node authentication and authorization mode\n",
        target_ru: "## Включение режима аутентификации и авторизации узлов\n",
    }
    assert check_href_parity(
        ru, en, en_page_path=page, docs_text_reader=files.get
    ) == []
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        docs_text_reader=files.get,
        en_baseline_text=f"See [nodes]({href_ru}).\n",
    ) == []


def test_explicit_ascii_anchor_cannot_use_heading_slug_remap_exception():
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target_en = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    target_ru = target_en.replace("/docs/en/", "/docs/ru/", 1)
    ru_frag = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    en_frag = "enabling-the-node-authentication-and-authorization-mode"
    href_ru = f"../../devops/concepts/node-authorization.md#{ru_frag}"
    href_en = f"../../devops/concepts/node-authorization.md#{en_frag}"
    files = {
        target_en: "## Enabling the node authentication and authorization mode\n",
        target_ru: (
            "## Включение режима аутентификации и авторизации узлов "
            f"{{#{ru_frag}}}\n"
        ),
    }
    assert check_href_parity(
        f"See [nodes]({href_ru}).\n",
        f"See [nodes]({href_en}).\n",
        en_page_path=page,
        docs_text_reader=files.get,
    ) == [f"href_parity: exact ASCII fragment changed: `{href_ru}` -> `{href_en}`"]


def test_ambiguous_heading_auto_slug_remap_stays_blocking():
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    target_en = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    target_ru = target_en.replace("/docs/en/", "/docs/ru/", 1)
    ru_frag = "rezhim-autentifikacii"
    en_frag = "authentication-mode"
    href_ru = f"../../devops/concepts/node-authorization.md#{ru_frag}"
    href_en = f"../../devops/concepts/node-authorization.md#{en_frag}"
    files = {
        target_en: "## Authentication mode\n## Authentication mode\n",
        target_ru: "## Режим аутентификации\n## Режим аутентификации\n",
    }
    assert check_href_parity(
        f"See [nodes]({href_ru}).\n",
        f"See [nodes]({href_en}).\n",
        en_page_path=page,
        docs_text_reader=files.get,
    ) == [f"href_parity: exact ASCII fragment changed: `{href_ru}` -> `{href_en}`"]


def test_href_parity_ascii_fragment_change_blocks_when_target_missing():
    """§8 exactness does not depend on whether the target currently resolves."""
    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    ru_frag = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    en_frag = "enabling-the-node-authentication-and-authorization-mode"
    href_ru = f"../../devops/concepts/node-authorization.md#{ru_frag}"
    href_en = f"../../devops/concepts/node-authorization.md#{en_frag}"
    ru = f"See [nodes]({href_ru}).\n"
    en = f"See [nodes]({href_en}).\n"
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        docs_text_reader=lambda _p: None,
    ) == [f"href_parity: exact ASCII fragment changed: `{href_ru}` -> `{href_en}`"]


def test_href_parity_accepts_job_dictionary_fragment_remap():
    from ydbdoc_review.validation.yfm_anchor import JobAnchorDictionary

    page = "ydb/docs/en/core/a.md"
    dictionary = JobAnchorDictionary()
    dictionary.lookup_or_insert("поля", "Fields")
    ru = "See [x](./other.md#поля).\n"
    en = "See [x](./other.md#fields).\n"
    files = {"ydb/docs/en/core/other.md": "## Fields {#fields}\n"}
    assert (
        check_href_parity(
            ru,
            en,
            en_page_path=page,
            docs_text_reader=files.get,
            dictionary=dictionary,
        )
        == []
    )


def test_href_parity_tip_baseline_does_not_grandfather_ascii_fragment_change():
    """#52077: tip baseline cannot hide a changed ASCII fragment (§8)."""
    page = "ydb/docs/en/core/security/authentication.md"
    ru = (
        "A [auth](../reference/configuration/auth_config.md#security-auth) and "
        "[tls](../reference/ydb-cli/connect.md#tls).\n"
    )
    en = (
        "A [auth](../reference/configuration/security_config.md#security-auth) and "
        "[tls](../reference/ydb-cli/connect.md#activated-profile).\n"
    )
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        docs_text_reader=lambda _p: "# ok\n",
        en_baseline_text=en,
    ) == [
        "href_parity: exact ASCII fragment changed: "
        "`../reference/ydb-cli/connect.md#tls` -> "
        "`../reference/ydb-cli/connect.md#activated-profile`"
    ]


def test_href_parity_reports_duplicate_ascii_fragment_changes_by_occurrence():
    """#52077: duplicate links produce one blocker per changed occurrence."""
    page = "ydb/docs/en/core/security/authentication.md"
    ru = (
        "See [TLS](../reference/ydb-cli/connect.md#tls).\n"
        "Also [TLS](../reference/ydb-cli/connect.md#tls).\n"
    )
    en = (
        "See [TLS](../reference/ydb-cli/connect.md#activated-profile).\n"
        "Also [TLS](../reference/ydb-cli/connect.md#activated-profile).\n"
    )
    issue = (
        "href_parity: exact ASCII fragment changed: "
        "`../reference/ydb-cli/connect.md#tls` -> "
        "`../reference/ydb-cli/connect.md#activated-profile`"
    )
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        docs_text_reader=lambda _p: "# activated-profile\n",
        en_baseline_text=en,
    ) == [issue, issue]


def test_pr_52077_reports_both_ascii_fragment_changes():
    """Regression fixture for both blocking link defects in PR #52077."""
    page = "ydb/docs/en/core/security/authentication.md"
    node_en = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    node_ru = node_en.replace("/docs/en/", "/docs/ru/", 1)
    ru = (
        "See [anonymous](../reference/configuration/auth_config.md#security-auth).\n"
        "See [nodes](../devops/concepts/node-authorization.md"
        "#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov).\n"
        "See [certificate](../reference/configuration/auth_config.md"
        "#certificate-auth-config).\n"
        "Connect with [TLS](../reference/ydb-cli/connect.md#tls).\n"
    )
    en = (
        "See [anonymous](../reference/configuration/security_config.md#security-auth).\n"
        "See [nodes](../devops/concepts/node-authorization.md"
        "#enabling-the-node-authentication-and-authorization-mode).\n"
        "See [certificate](../reference/configuration/auth_config.md"
        "#iam-auth-config).\n"
        "Connect with [TLS](../reference/ydb-cli/connect.md#activated-profile).\n"
    )
    expected = [
        "href_parity: exact ASCII fragment changed: "
        "`../reference/configuration/auth_config.md#certificate-auth-config` -> "
        "`../reference/configuration/auth_config.md#iam-auth-config`",
        "href_parity: exact ASCII fragment changed: "
        "`../reference/ydb-cli/connect.md#tls` -> "
        "`../reference/ydb-cli/connect.md#activated-profile`",
    ]
    files = {
        node_en: "## Enabling the node authentication and authorization mode\n",
        node_ru: "## Включение режима аутентификации и авторизации узлов\n",
    }
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        docs_text_reader=files.get,
        en_baseline_text=en,
    ) == expected

    classified = run_file_heuristics_classified(
        ru,
        en,
        normalized_source_text=ru,
        source_lang="ru",
        target_lang="en",
        source_file=page.replace("/docs/en/", "/docs/ru/", 1),
        docs_text_reader=files.get,
        en_baseline_text=en,
    )
    assert classified.blocking == expected
    assert classified.warnings == []
    assert classified.info == []


def test_href_parity_accepts_path_only_redirect_with_same_ascii_fragment():
    ru = "See [auth](../old/auth_config.md#certificate-auth-config).\n"
    en = "See [auth](../new/auth_config.md#certificate-auth-config).\n"
    assert check_href_parity(ru, en, en_baseline_text=en) == []


def test_href_parity_rejects_path_and_ascii_fragment_change_despite_baseline():
    ru = "See [auth](../old/auth_config.md#certificate-auth-config).\n"
    en = "See [auth](../new/auth_config.md#iam-auth-config).\n"
    assert check_href_parity(ru, en, en_baseline_text=en) == [
        "href_parity: exact ASCII fragment changed: "
        "`../old/auth_config.md#certificate-auth-config` -> "
        "`../new/auth_config.md#iam-auth-config`"
    ]


def test_href_parity_same_path_ambient_extra_does_not_shift_exact_fragment():
    ru = "See [TLS](connect.md#tls).\n"
    en = "See [profile](connect.md#activated-profile), then [TLS](connect.md#tls).\n"
    assert check_href_parity(ru, en, en_baseline_text=en) == []


def test_href_parity_grandfathers_tip_only_extras():
    page = "ydb/docs/en/core/security/index.md"
    ru = "See [auth](./authentication.md).\n"
    en = (
        "See [auth](./authentication.md) and "
        "[level](../concepts/glossary.md#access-level).\n"
    )
    assert (
        check_href_parity(
            ru,
            en,
            en_page_path=page,
            en_baseline_text=en,
        )
        == []
    )


def test_restore_non_unique_baseline_slot_is_nonblocking():
    """P9c: ambiguous tip baseline LinkSlot owner must not hard-block."""
    from ydbdoc_review.validation.href_parity import _restore_link_in_aligned_segment

    # Two paragraphs each own the same baseline label+href → non-unique ordinal.
    baseline = (
        "See the UI overview.\n\n"
        "Also see the UI overview.\n"
    )
    # Force the protected-href scan path: baseline must contain real md links.
    baseline = (
        "See [UI](../../reference/ydb-ui/index.md).\n\n"
        "Also [UI](../../reference/ydb-ui/index.md).\n"
    )
    translated = "See UI.\n\nAlso UI.\n"
    result = _restore_link_in_aligned_segment(
        translated,
        baseline,
        baseline_label="UI",
        baseline_href="../../reference/ydb-ui/index.md",
        current_href="../../reference/embedded-ui/index.md",
    )
    assert result.ok
    assert result.issues == ()


def test_href_parity_tip_preserved_without_source_baseline():
    """P9c: candidate/verify gate (en baseline only) grandfathers tip-preserved EN."""
    page = "ydb/docs/en/core/devops/configuration-management/compare-configs.md"
    ru = (
        "See [v2](../../contributor/configuration-v2.md) and "
        "[ui](../../reference/ydb-ui/index.md).\n"
    )
    en = "See [ui](../../reference/embedded-ui/index.md).\n"
    assert (
        check_href_parity(
            ru,
            en,
            en_page_path=page,
            en_baseline_text=en,
        )
        == []
    )
    # Dual-baseline translate still flags newly added RU when EN stays at tip.
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        source_baseline_text="See [ui](../../reference/ydb-ui/index.md).\n",
        en_baseline_text=en,
    )


def test_pr_51761_proven_auto_slug_remap_precedes_tip_baseline_grandfather():
    """A proven implicit heading remap is allowed before grandfathering."""
    from ydbdoc_review.validation.heuristics import run_file_heuristics_classified

    page = "ydb/docs/en/core/reference/configuration/client_certificate_authorization.md"
    page_ru = page.replace("/docs/en/", "/docs/ru/", 1)
    target_en = "ydb/docs/en/core/devops/concepts/node-authorization.md"
    target_ru = target_en.replace("/docs/en/", "/docs/ru/", 1)
    ru_frag = "vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov"
    en_frag = "enabling-the-node-authentication-and-authorization-mode"
    href_tip_ru = f"../../devops/concepts/node-authorization.md#{ru_frag}"
    href_tip_en = f"../../devops/concepts/node-authorization.md#{en_frag}"
    href_base = (
        "../../devops/deployment-options/manual/node-authorization.md#"
        f"{ru_frag}"
    )
    filler = "[TLS](https://example.com) "
    ru_tip = filler * 8 + f"See [nodes]({href_tip_ru}).\n"
    en_tip = filler * 8 + f"See [nodes]({href_tip_en}).\n"
    ru_base = filler * 8 + f"See [nodes]({href_base}).\n"
    en_merge_base = filler * 8 + f"See [nodes]({href_base}).\n"
    files = {
        target_en: "## Enabling the node authentication and authorization mode\n",
        target_ru: "## Включение режима аутентификации и авторизации узлов\n",
    }
    reader = files.get

    assert check_href_parity(
        ru_tip,
        en_tip,
        en_page_path=page,
        docs_text_reader=reader,
        en_baseline_text=en_tip,
        source_baseline_text=ru_base,
    ) == []

    classified = run_file_heuristics_classified(
        ru_tip,
        en_tip,
        normalized_source_text=ru_tip,
        source_file=page_ru,
        docs_text_reader=reader,
        en_baseline_text=en_merge_base,
        source_baseline_text=ru_base,
    )
    assert classified.blocking == []


def test_href_parity_50976_ignores_localized_external_link_with_same_label():
    ru = (
        "[TLS](https://ru.wikipedia.org/wiki/Transport_Layer_Security) "
        "и [интерфейс](../../reference/ydb-ui/index.md).\n"
    )
    en = (
        "[TLS](https://en.wikipedia.org/wiki/Transport_Layer_Security) "
        "and [Embedded UI](../../reference/ydb-ui/index.md).\n"
    )

    assert check_href_parity(ru, en) == []


def test_href_parity_rejects_reordered_duplicate_path_fragments():
    ru = "[First](foo.md#a) [Second](foo.md#b)\n"
    en = "[First](foo.md#b) [Second](foo.md#a)\n"
    assert check_href_parity(ru, en) == [
        "href_parity: same link label points to a different internal href"
    ]


def test_href_parity_rejects_duplicate_swap_with_translated_labels():
    ru = "[First](foo.md#a) [Second](foo.md#b)\n"
    en = "[Первый](foo.md#b) [Второй](foo.md#a)\n"
    assert check_href_parity(ru, en) == ["href_parity: repeated-path links have different order"]


def test_href_parity_does_not_collapse_duplicate_labels():
    ru = "[Config](a.md) [Config](b.md)\n"
    en = "[Config](b.md) [Config](a.md)\n"
    assert check_href_parity(ru, en) == []


def test_href_parity_rejects_declared_but_unrelated_en_fragment():
    page = "ydb/docs/en/core/security/index.md"
    ru = "[SID](./authorization.md#sid)\n"
    en = "[SID](./authorization.md#permissions)\n"
    files = {
        "ydb/docs/en/core/security/authorization.md": (
            "## User {#user}\n## Permissions {#permissions}\n"
        ),
    }
    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        docs_text_reader=files.get,
        en_baseline_text="[SID](./authorization.md#user)\n",
    )


def test_href_parity_flags_extra_en_link():
    ru = "See [{#T}](foo.md#a).\n"
    en = "See [{#T}](foo.md#a) and [more](bar.md#b).\n"
    msgs = check_href_parity(ru, en)
    assert msgs and "extra in EN" in msgs[0]
    assert "bar.md#b" in msgs[0]


def test_href_parity_accepts_percent_encoded_unicode_fragment():
    ru = "[users](../dev/system-views.md#информация-о-пользователях-users)\n"
    en = (
        "[users](../dev/system-views.md#%D0%B8%D0%BD%D1%84%D0%BE%D1%80%D0%BC%D0%B0%D1%86%D0%B8%D1%8F-"
        "%D0%BE-%D0%BF%D0%BE%D0%BB%D1%8C%D0%B7%D0%BE%D0%B2%D0%B0%D1%82%D0%B5%D0%BB%D1%8F%D1%85-users)\n"
    )

    assert check_href_parity(ru, en) == []


def test_href_parity_accepts_pr_50976_localized_reachable_path():
    """#50976: RU moved embedded UI before EN; keep the reachable EN twin."""
    page = "ydb/docs/en/core/reference/configuration/monitoring_config.md"
    ru = "[YDB Monitoring](../embedded-ui/ydb-monitoring.md)\n"
    en = "[YDB Monitoring](../ydb-ui/ydb-monitoring.md)\n"

    assert (
        check_href_parity(
            ru,
            en,
            en_page_path=page,
            en_toc_reachable=frozenset({"ydb/docs/en/core/reference/ydb-ui/ydb-monitoring.md"}),
        )
        == []
    )


def test_href_parity_rejects_localized_path_when_en_target_is_unreachable():
    page = "ydb/docs/en/core/reference/configuration/monitoring_config.md"
    ru = "[YDB Monitoring](../embedded-ui/ydb-monitoring.md)\n"
    en = "[YDB Monitoring](../ydb-ui/ydb-monitoring.md)\n"

    assert check_href_parity(
        ru,
        en,
        en_page_path=page,
        en_toc_reachable=frozenset(),
    )


def test_finalize_restores_exact_source_links_and_cyrillic_code_atoms():
    from ydbdoc_review.harness.render import finalize_en_target

    source = "See [SID](authorization.md#sid) in `Имя=Значение,...@<domain>` notation.\n"
    translated = "See [SID](authorization.md#user) in `Name=Value,...@<domain>` notation.\n"

    assert finalize_en_target(translated, source) == source


def test_finalize_does_not_restore_unreachable_source_href():
    from ydbdoc_review.harness.render import finalize_en_target

    warnings: list[str] = []
    result = finalize_en_target(
        "See the section [Missing](missing.md).\n",
        "См. раздел [Missing](missing.md).\n",
        file_path="ydb/docs/ru/core/a.md",
        en_toc_reachable=frozenset(),
        out_warnings=warnings,
    )

    assert "missing.md" not in result
    assert any("removed 1" in warning for warning in warnings)


def test_finalize_does_not_restore_reordered_plain_code_atoms():
    from ydbdoc_review.harness.render import finalize_en_target

    source = "Используйте `первый` перед `второй`.\n"
    translated = "Use `second` after `first`.\n"

    assert finalize_en_target(translated, source) == translated


def test_finalize_restores_unique_structured_atom_despite_count_drift():
    from ydbdoc_review.harness.render import finalize_en_target

    source = "Задайте `Имя=Значение,...@<domain>` и `режим`.\n"
    translated = "Set `Name=Value,...@<domain>`.\n"

    assert finalize_en_target(translated, source) == ("Set `Имя=Значение,...@<domain>`.\n")


def test_finalize_does_not_match_unrelated_assignment_atoms():
    from ydbdoc_review.harness.render import finalize_en_target

    assert (
        finalize_en_target(
            "Use port `x=y`.\n",
            "Используйте `режим=строгий` и `порт`.\n",
        )
        == "Use port `x=y`.\n"
    )


def test_finalize_does_not_match_unrelated_colon_atoms():
    from ydbdoc_review.harness.render import finalize_en_target

    assert (
        finalize_en_target(
            "Use `key:value`.\n",
            "Используйте `имя:значение`.\n",
        )
        == "Use `key:value`.\n"
    )


def test_finalize_refuses_ambiguous_certificate_notation_atoms():
    from ydbdoc_review.harness.render import finalize_en_target

    source = "Use `Имя=Значение,...@<domain>` or `Имя=Значение,...@<domain>`.\n"
    translated = "Use `Name=Value,...@<domain>` or `Name=Value,...@<domain>`.\n"

    assert finalize_en_target(translated, source) == translated


def test_finalize_refuses_different_certificate_shaped_target_atom():
    from ydbdoc_review.harness.render import finalize_en_target

    source = "Use `Имя=Значение,...@<domain>`.\n"
    translated = "Use `Subject=Issuer,...@<domain>`.\n"

    assert finalize_en_target(translated, source) == translated


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
    auth_ru = "## Аутентификация через LDAP {#ldap}\n"
    auth_baseline = "## Authentication using LDAP directory {#ldap-auth-provider}\n"
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
        ru_text=auth_ru,
        en_baseline_text=auth_baseline,
    )
    assert any(m.startswith("inbound_fragment:") for m in msgs)
    assert any("ldap-auth-provider" in m for m in msgs)
    assert not any("authentication.md#ldap`" in m for m in msgs)


def test_inbound_ignores_same_basename_other_dirs():
    """#49451: index.md#frag on another section is not inbound to config-v2."""
    page = "ydb/docs/en/core/devops/configuration-management/configuration-v2/index.md"
    files = {
        page: "# Config V2\n",
        "ydb/docs/en/core/analyst/datasets/_includes/intro.md": (
            "See [x](index.md#general-info).\n"
        ),
    }
    assert (
        check_inbound_fragments(
            page,
            files[page],
            en_paths=list(files),
            read_text=files.get,
            ru_text="# Конфигурация V2\n",
        )
        == []
    )


def test_inbound_ignores_frag_absent_from_ru_and_baseline():
    """Ambient EN typos (#tablets vs RU {#tablet}) must not block translation QA."""
    glossary = "ydb/docs/en/core/concepts/glossary.md"
    files = {
        glossary: "### Tablet {#tablet}\n",
        "ydb/docs/en/core/concepts/architecture/index.md": (
            "See [tablet](../glossary.md#tablets).\n"
        ),
    }
    assert (
        check_inbound_fragments(
            glossary,
            files[glossary],
            en_paths=list(files),
            read_text=files.get,
            ru_text="### Таблетка {#tablet}\n",
            en_baseline_text="### Tablet {#tablet}\n",
        )
        == []
    )


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


def test_restore_md_link_hrefs_fixes_wrong_path_by_position():
    """#49451: EN pointed at secondary_index.md#example instead of min_max."""
    from ydbdoc_review.validation.href_parity import restore_md_link_hrefs

    ru = (
        "Для [таблицы `events`](../../yql/reference/syntax/create_table/"
        "min_max_index.md#example) значения `[5, 13]`.\n"
    )
    en = (
        "For the [`events` table](../../yql/reference/syntax/create_table/"
        "secondary_index.md#example) values `[5, 13]`.\n"
    )
    fixed = restore_md_link_hrefs(en, ru)
    assert "min_max_index.md#example" in fixed
    assert "secondary_index.md#example" not in fixed
    assert check_href_parity(ru, fixed) == []


def test_restore_md_link_hrefs_preserves_semantic_links_after_reorder():
    """#50797: equal href multisets may move with their translated list items."""
    from ydbdoc_review.validation.href_parity import restore_md_link_hrefs

    ru = (
        "- [Authentication](authentication.md) and "
        "[authorization](authorization.md).\n"
        "  - [Device authentication](authentication.md#device-auth) uses a "
        "[client certificate](../concepts/glossary.md#client-certificate).\n"
    )
    en = (
        "- [Device authentication](authentication.md#device-auth) uses a "
        "[client certificate](../concepts/glossary.md#client-certificate).\n"
        "- [Authentication](authentication.md) and "
        "[authorization](authorization.md).\n"
    )

    assert restore_md_link_hrefs(en, ru) == en
    assert check_href_parity(ru, en) == []


def test_restore_md_link_hrefs_wraps_see_the_section_plain_text():
    """#49451: glossary dropped architecture/metadata-services.md links."""
    from ydbdoc_review.validation.href_parity import restore_md_link_hrefs

    ru = (
        "Подробнее — в разделе "
        "[Сервисы распространения метаданных](architecture/metadata-services.md).\n"
    )
    en = "For more details, see the section Metadata distribution services.\n"
    fixed = restore_md_link_hrefs(en, ru)
    assert "[Metadata distribution services](architecture/metadata-services.md)" in fixed
    assert check_href_parity(ru, fixed) == []


def test_insert_missing_autotitle_list_items_splices_neighbor():
    """#49451: critic dropped static-group-self-heal.md from the V2 index."""
    from ydbdoc_review.validation.href_parity import (
        insert_missing_autotitle_list_items,
    )

    ru = (
        "- [{#T}](state-storage-move.md)\n"
        "- [{#T}](static-group-self-heal.md)\n"
        "- [{#T}](static-group-move.md)\n"
    )
    en = "- [{#T}](state-storage-move.md)\n- [{#T}](static-group-move.md)\n"
    fixed = insert_missing_autotitle_list_items(en, ru)
    assert "static-group-self-heal.md" in fixed
    assert check_href_parity(ru, fixed) == []


def test_insert_missing_skips_unreachable_en_targets():
    """#49451: do not re-insert State Storage reconfig when EN has no page."""
    from ydbdoc_review.validation.href_parity import (
        insert_missing_autotitle_list_items,
    )

    ru = (
        "- [{#T}](state-storage-move.md)\n"
        "- [{#T}](state-storage-reconfiguration.md)\n"
        "- [{#T}](static-group-self-heal.md)\n"
    )
    en = "- [{#T}](state-storage-move.md)\n- [{#T}](static-group-self-heal.md)\n"
    # Only move + self-heal are reachable; reconfiguration is not.
    reachable = frozenset(
        {
            "ydb/docs/en/core/devops/configuration-management/configuration-v2/"
            "state-storage-move.md",
            "ydb/docs/en/core/devops/configuration-management/configuration-v2/"
            "static-group-self-heal.md",
        }
    )
    fixed = insert_missing_autotitle_list_items(
        en,
        ru,
        en_page_path=("ydb/docs/en/core/devops/configuration-management/configuration-v2/index.md"),
        en_toc_reachable=reachable,
    )
    assert "state-storage-reconfiguration.md" not in fixed
    assert "static-group-self-heal.md" in fixed


def test_href_parity_allows_reachable_en_extras():
    """#49451: EN may keep self-heal when source-PR RU snapshot omits it."""
    from ydbdoc_review.validation.href_parity import check_href_parity

    ru = (
        "- [{#T}](state-storage-move.md)\n"
        "- [{#T}](state-storage-reconfiguration.md)\n"
        "- [{#T}](static-group-move.md)\n"
    )
    en = (
        "- [{#T}](state-storage-move.md)\n"
        "- [{#T}](static-group-self-heal.md)\n"
        "- [{#T}](static-group-move.md)\n"
    )
    page = "ydb/docs/en/core/devops/configuration-management/configuration-v2/index.md"
    reachable = frozenset(
        {
            f"{page.rsplit('/', 1)[0]}/state-storage-move.md",
            f"{page.rsplit('/', 1)[0]}/static-group-self-heal.md",
            f"{page.rsplit('/', 1)[0]}/static-group-move.md",
        }
    )
    assert (
        check_href_parity(
            ru,
            en,
            ignore_basenames={"state-storage-reconfiguration.md"},
            en_page_path=page,
            en_toc_reachable=reachable,
        )
        == []
    )


def test_restore_autotitle_force_exact_skips_unreachable():
    from ydbdoc_review.validation.autotitle_hrefs import restore_autotitle_hrefs

    ru = (
        "- [{#T}](state-storage-move.md)\n"
        "- [{#T}](state-storage-reconfiguration.md)\n"
        "- [{#T}](static-group-self-heal.md)\n"
    )
    # Critic swapped self-heal for reconfig; bare leftover from a prior strip.
    en = "- [{#T}](state-storage-move.md)\n- {#T}\n- [{#T}](static-group-self-heal.md)\n"
    reachable = frozenset(
        {
            "ydb/docs/en/core/devops/configuration-management/configuration-v2/"
            "state-storage-move.md",
            "ydb/docs/en/core/devops/configuration-management/configuration-v2/"
            "static-group-self-heal.md",
        }
    )
    fixed = restore_autotitle_hrefs(
        en,
        ru,
        force_exact=True,
        en_page_path=("ydb/docs/en/core/devops/configuration-management/configuration-v2/index.md"),
        en_toc_reachable=reachable,
    )
    assert "state-storage-reconfiguration.md" not in fixed
    assert "static-group-self-heal.md" in fixed


def test_prefer_resolvable_en_hrefs_keeps_tip_valid_over_missing():
    from ydbdoc_review.validation.href_parity import prefer_resolvable_en_hrefs

    previous = (
        "See [node-broker](../devops/configuration-management/configuration-v1/"
        "node-authorization.md).\n"
    )
    proposed = (
        "See [node-broker](../devops/deployment-options/manual/"
        "node-authorization.md).\n"
    )
    files = {
        "ydb/docs/en/core/devops/configuration-management/configuration-v1/"
        "node-authorization.md": "ok\n",
    }
    page = "ydb/docs/en/core/security/authentication.md"
    assert (
        prefer_resolvable_en_hrefs(
            proposed, previous, en_page_path=page, read_text=files.get
        )
        == previous
    )


def test_prefer_resolvable_en_hrefs_keeps_same_fragment_on_valid_tip_path():
    from ydbdoc_review.validation.href_parity import prefer_resolvable_en_hrefs

    page = "ydb/docs/en/core/security/authentication.md"
    proposed = "See [auth](../reference/configuration/auth_config.md#security-auth).\n"
    previous = "See [auth](../reference/configuration/security_config.md#security-auth).\n"
    files = {
        "ydb/docs/en/core/reference/configuration/auth_config.md": "# auth_config\n",
        "ydb/docs/en/core/reference/configuration/security_config.md": (
            "## Authentication {#security-auth}\n"
        ),
    }
    assert (
        prefer_resolvable_en_hrefs(
            proposed, previous, en_page_path=page, read_text=files.get
        )
        == previous
    )


def test_overlay_internal_md_hrefs_prefers_tip_by_label():
    from ydbdoc_review.validation.href_parity import overlay_internal_md_hrefs

    merge = "[node-broker](../devops/deployment-options/manual/node-authorization.md)\n"
    tip = (
        "[node-broker](../devops/configuration-management/configuration-v1/"
        "node-authorization.md)\n"
    )
    assert overlay_internal_md_hrefs(merge, tip) == tip


def test_inverted_mirror_delta_then_prefer_resolvable_keeps_tip_en():
    from ydbdoc_review.validation.href_parity import (
        apply_localized_mirror_delta,
        prefer_resolvable_en_hrefs,
    )

    ru_tip = (
        "[node-broker](../devops/configuration-management/configuration-v1/"
        "node-authorization.md)\n"
    )
    ru_merge = (
        "[node-broker](../devops/deployment-options/manual/node-authorization.md)\n"
    )
    en = ru_tip.replace("node-broker", "node-broker")  # same href
    localized = apply_localized_mirror_delta(ru_tip, ru_merge, en)
    assert localized == ru_merge
    files = {
        "ydb/docs/en/core/devops/configuration-management/configuration-v1/"
        "node-authorization.md": "ok\n",
    }
    page = "ydb/docs/en/core/security/authentication.md"
    assert (
        prefer_resolvable_en_hrefs(
            localized, en, en_page_path=page, read_text=files.get
        )
        == en
    )


def test_prefer_resolvable_keeps_tip_devops_ydb_ui_over_stale_maintenance():
    """#52055 / P9d: merge-commit checkout must not keep tombstone tip-mirror hrefs.

    When tip EN already uses devops/ydb-ui targets but an inverted RU mirror
    delta proposes legacy maintenance/embedded-ui paths (missing on tip), keep
    the tip-resolvable hrefs.
    """
    from ydbdoc_review.validation.href_parity import (
        apply_localized_mirror_delta,
        prefer_resolvable_en_hrefs,
    )

    page = "ydb/docs/en/core/reference/observability/tracing/setup.md"
    tip_en = (
        "See [dynamic configuration](../../../devops/configuration-management/"
        "configuration-v1/dynamic-config.md).\n"
        "As with [logs](../../../reference/ydb-ui/logs.md), ok.\n"
    )
    # Historical merge RU still carries pre-rename paths (tombstones on tip).
    ru_tip = tip_en  # tip RU already renamed with EN
    ru_merge = (
        "See [dynamic configuration](../../../maintenance/manual/"
        "dynamic-config.md).\n"
        "As with [logs](../../../reference/embedded-ui/logs.md), ok.\n"
    )
    localized = apply_localized_mirror_delta(ru_tip, ru_merge, tip_en)
    assert localized == ru_merge  # pure href mirror would pollute tip EN
    files = {
        "ydb/docs/en/core/devops/configuration-management/configuration-v1/"
        "dynamic-config.md": "# Dynamic\n",
        "ydb/docs/en/core/reference/ydb-ui/logs.md": "# Logs\n",
    }
    assert (
        prefer_resolvable_en_hrefs(
            localized, tip_en, en_page_path=page, read_text=files.get
        )
        == tip_en
    )
