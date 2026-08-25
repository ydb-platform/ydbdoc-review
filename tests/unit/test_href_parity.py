"""§6.174: RU↔EN href / anchor parity and inbound fragment checks."""

from __future__ import annotations

from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.href_parity import (
    apply_href_only_delta,
    check_heading_anchor_parity,
    check_href_parity,
    check_inbound_fragments,
    collect_internal_hrefs,
    is_href_only_change,
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


def test_href_only_change_ignores_writer_trailing_blank_normalization():
    before = "See [node](old.md).\n\n"
    after = "See [node](new.md).\n"

    assert is_href_only_change(before, after)


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


def test_href_parity_accepts_pr_50976_declared_en_fragments():
    page = "ydb/docs/en/core/security/index.md"
    ru = "[SID](./authorization.md#sid)\n"
    en = "[SID](./authorization.md#user)\n"
    files = {
        "ydb/docs/en/core/security/authorization.md": "## User {#user}\n",
    }
    assert (
        check_href_parity(
            ru,
            en,
            en_page_path=page,
            docs_text_reader=files.get,
            en_baseline_text=en,
        )
        == []
    )


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
