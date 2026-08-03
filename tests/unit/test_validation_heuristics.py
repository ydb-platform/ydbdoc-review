"""Tests for Phase E validation heuristics."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.validation.heuristics import (
    bump_verdict_for_blocking_heuristics,
    bump_verdict_for_heuristics,
    check_cyrillic_in_en,
    check_cyrillic_in_en_all_fences,
    check_fence_parity,
    check_heading_parity,
    check_length_ratio,
    check_list_tab_parity,
    check_md_link_parity,
    check_unrestored_placeholders,
    run_file_heuristics,
    run_file_heuristics_classified,
    validate_navigation_merge_warnings,
    validate_redirect_merge_warnings,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation


def test_cyrillic_in_en_detects_prose():
    warnings = check_cyrillic_in_en("Hello привет world", target_lang="en")
    assert len(warnings) == 1


def test_unrestored_placeholder_blocks():
    """§6.163: leftover protect markers in final EN are blocking."""
    text = (
        "The ⟦V1⟧ cluster uses [SIDs](%E2%9F%A6U1%E2%9F%A7) and ⟦C1⟧ code.\n"
    )
    msgs = check_unrestored_placeholders(text, target_lang="en")
    assert len(msgs) == 1
    assert msgs[0].startswith("unrestored_placeholder:")
    assert "⟦V1⟧" in msgs[0]
    assert "%E2%9F%A6U1%E2%9F%A7" in msgs[0]
    classified = run_file_heuristics_classified(
        "Секция.\n",
        text,
        normalized_source_text="Секция.\n",
        source_lang="ru",
        target_lang="en",
    )
    assert any(m.startswith("unrestored_placeholder:") for m in classified.blocking)


def test_unrestored_placeholder_blocks_glossary_v2():
    """§6.164 / #48595: glossary leftover ``⟦V2⟧`` must block merge."""
    text = (
        "A **client certificate** confirms identity when interacting with ⟦V2⟧.\n"
    )
    msgs = check_unrestored_placeholders(text, target_lang="en")
    assert any("⟦V2⟧" in m for m in msgs)
    classified = run_file_heuristics_classified(
        "Сертификат.\n",
        text,
        normalized_source_text="Сертификат.\n",
        source_lang="ru",
        target_lang="en",
    )
    assert any(m.startswith("unrestored_placeholder:") for m in classified.blocking)


def test_cyrillic_in_yaml_fence_blocks():
    """§6.164 / #48595: RU angle-brackets in yaml examples must block."""
    text = dedent(
        """
        Intro paragraph with enough English words for length checks here.

        ```yaml
        client_certificate_authorization:
          default_group: <SID по умолчанию>
          groups:
            - member_groups: <массив SID>
              subject_terms:
                - short_name: <имя компонента Subject Name>
        ```
        """
    )
    msgs = check_cyrillic_in_en_all_fences(text, target_lang="en")
    assert msgs
    assert any("по умолчанию" in m for m in msgs)
    assert all(m.startswith("cyrillic_in_code_fence:") for m in msgs if "ещё" not in m)
    # Prose check must still miss fence Cyrillic (historical blind spot).
    assert check_cyrillic_in_en(text, target_lang="en") == []
    classified = run_file_heuristics_classified(
        "Секция настроек.\n",
        text,
        normalized_source_text="Секция настроек.\n",
        source_lang="ru",
        target_lang="en",
    )
    assert any(m.startswith("cyrillic_in_code_fence:") for m in classified.blocking)
    assert not any(m.startswith("cyrillic_in_code_fence:") for m in classified.warnings)


def test_md_link_parity_ignores_self_basename_link():
    """RU self-link to the same file basename is not an EN gap (§6.147)."""
    ru = "See [this page](hive_config.md) and [Hive](../../contributor/hive.md).\n"
    en = "See [Hive](../../contributor/hive.md).\n"
    warnings = check_md_link_parity(
        ru,
        en,
        source_lang="ru",
        target_lang="en",
        source_file="ydb/docs/ru/core/reference/configuration/hive_config.md",
    )
    assert warnings == []


def test_verify_realign_message_is_info_not_blocking():
    from ydbdoc_review.validation.heuristics import _classify_heuristic

    assert (
        _classify_heuristic(
            "verify_realign: rebuilt EN from RU due to segment alignment mismatch"
        )
        == "info"
    )


def test_strip_unreachable_links_message_is_info_not_blocking():
    """§6.152 / #48123: Variant A strip is intentional repair, not a red QA."""
    from ydbdoc_review.validation.heuristics import _classify_heuristic

    assert (
        _classify_heuristic(
            "strip_unreachable_links: removed 5 internal href(s) outside EN toc graph"
        )
        == "info"
    )
    assert (
        _classify_heuristic(
            "strip_unreachable_links_failed: AttributeError: boom"
        )
        == "warnings"
    )


def test_md_link_parity_flags_missing_en_link():
    ru = "- [logs](debug-logs.md)\n- [otel logs](debug-logs-otel.md)\n"
    en = "- [logs](debug-logs.md)\n"
    warnings = check_md_link_parity(ru, en, source_lang="ru", target_lang="en")
    assert len(warnings) == 1
    assert "debug-logs-otel.md" in warnings[0]
    assert "md_link_parity" in warnings[0]


def test_md_link_parity_ignores_links_outside_en_toc_reachable():
    """Strip (§6.107) drops unreachable EN links; parity must not fail QA (§6.114)."""
    ru = (
        "See [watermarks](watermarks.md) and [patterns](patterns.md).\n"
    )
    en = "See watermarks and [patterns](patterns.md).\n"
    reachable = frozenset(
        {
            "ydb/docs/en/core/concepts/streaming-query/patterns.md",
        }
    )
    warnings = check_md_link_parity(
        ru,
        en,
        source_lang="ru",
        target_lang="en",
        source_file="ydb/docs/ru/core/concepts/streaming-query/index.md",
        en_toc_reachable=reachable,
    )
    assert warnings == []


def test_cyrillic_in_en_ignores_fenced_code():
    text = "Intro\n\n```\nпривет\n```\n"
    assert check_cyrillic_in_en(text, target_lang="en") == []


def test_cyrillic_in_en_fence_comments_blocks_on_line_comments():
    """§6.164: residual Cyrillic in fences is blocking, not a soft warning."""
    text = "Intro\n\n```go\n// настраиваем провайдер\n```\n"
    classified = run_file_heuristics_classified(
        text,
        text,
        normalized_source_text=text,
        source_lang="ru",
        target_lang="en",
    )
    assert any(
        w.startswith("cyrillic_in_fence:") or w.startswith("cyrillic_in_code_fence:")
        for w in classified.blocking
    )
    assert not any(
        w.startswith("cyrillic_in_fence:") or w.startswith("cyrillic_in_code_fence:")
        for w in classified.warnings
    )


def test_cyrillic_skipped_for_ru_target():
    assert check_cyrillic_in_en("привет", target_lang="ru") == []


def test_fence_parity_mismatch():
    src = "A\n\n```\ncode\n```\n"
    tgt = "A\n"
    warnings = check_fence_parity(src, tgt)
    assert any("fence_parity" in w for w in warnings)


def test_fence_parity_ignores_triple_backticks_inside_block_body():
    block = "```bash\necho '```'\nmore\n```\n"
    assert check_fence_parity(block, block) == []


def test_heading_parity():
    src = "# One\n\n## Two\n"
    tgt = "# One\n"
    assert check_heading_parity(src, tgt)


def test_list_tab_parity_match():
    block = "{% list tabs %}\n\n- Tab\n\n  Body.\n\n{% endlist %}\n"
    assert check_list_tab_parity(block, block) == []


def test_list_tab_parity_mismatch():
    src = "{% list tabs %}\n\n- A\n\n  One.\n\n{% endlist %}\n"
    tgt = "No tabs here.\n"
    warnings = check_list_tab_parity(src, tgt)
    assert len(warnings) == 1
    assert "list_tab_parity" in warnings[0]
    assert "source 1 tab blocks vs target 0" in warnings[0]


def test_length_ratio_short_text_skipped():
    assert check_length_ratio("Hi", "Hello", source_lang="ru", target_lang="en") == []


def test_length_ratio_out_of_bounds():
    src = "word " * 50
    tgt = "x" * 45
    warnings = check_length_ratio(src, tgt, source_lang="ru", target_lang="en")
    assert warnings and "length_ratio" in warnings[0]


def test_run_file_heuristics_flags_broken_wikipedia_link():
    src = (
        "См. [CoW](https://ru.wikipedia.org/wiki/Копирование_при_записи).\n"
        + "Текст. " * 30
    )
    tgt = (
        "See [CoW](https://en.wikipedia.org/wiki/%D0%9A%D0%BE%D0%BF%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5_%D0%BF%D1%80%D0%B8_%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D0%B8).\n"
        + "Text. " * 30
    )
    classified = run_file_heuristics_classified(
        src, tgt, normalized_source_text=src, source_lang="ru", target_lang="en"
    )
    assert any("link_locale:" in w for w in classified.blocking)


def test_run_file_heuristics_combined():
    src = "# Title\n\n" + ("Paragraph. " * 30) + "\n\n```\nru\n```\n"
    tgt = "# Title\n\n" + ("Paragraph. " * 30) + "\n\n```\nen\n```\n"
    warnings = run_file_heuristics(src, tgt, source_lang="ru", target_lang="en")
    assert isinstance(warnings, list)


def test_bump_verdict_for_heuristics():
    assert bump_verdict_for_heuristics("ok", ["x"]) == "warnings"
    assert bump_verdict_for_heuristics("blocked", ["x"]) == "blocked"


def test_bump_verdict_for_blocking_heuristics():
    assert bump_verdict_for_blocking_heuristics("ok", ["fence_parity: x"]) == "blocked"
    assert bump_verdict_for_blocking_heuristics("warnings", []) == "warnings"


def test_run_file_heuristics_classified_ru_source_is_info():
    ru = "x --config-dir/opt/ydb/cfg\n"
    norm = normalize_ru_source_for_translation(ru)
    c = run_file_heuristics_classified(ru, "x --config-dir /opt/ydb/cfg\n", normalized_source_text=norm)
    assert c.info and not c.blocking


def test_validate_navigation_merge_warnings_toc():
    ru = dedent("""
        items:
        - name: A
          href: a.md
    """).strip()
    en = ru.replace("name: A", "name: B")
    warnings = validate_navigation_merge_warnings(
        "ydb/docs/ru/toc.yaml",
        ru,
        en,
        en_main_yaml=ru,
        translate_scope=set(),
    )
    assert isinstance(warnings, list)


def test_validate_redirect_merge_warnings_clean():
    ru = dedent("""
        - from: /old
          to: /new
    """).strip()
    en = ru
    warnings = validate_redirect_merge_warnings(
        ru,
        en,
        translate_from_paths=set(),
        en_main_yaml=en,
    )
    assert warnings == []


def test_validate_navigation_merge_warnings_redirect():
    ru = dedent("""
        - from: /old
          to: /new
        - from: /brand-new
          to: /target
    """).strip()
    en = dedent("""
        - from: /old
          to: /new
    """).strip()
    warnings = validate_navigation_merge_warnings(
        "ydb/docs/ru/redirects.yaml",
        ru,
        en,
        en_main_yaml=en,
        translate_scope=set(),
    )
    assert warnings
    assert any("missing_from" in w for w in warnings)


def test_heading_parity_counts_indented_headings_inside_yfm_if():
    """§6.156: RU headings indented in ``{% if %}`` must still count."""
    ru = (
        "{% if feature_group_by_rollup_cube %}\n"
        "  ## ROLLUP {#rollup}\n"
        "{% endif %}\n"
        "## Other\n"
    )
    en = (
        "{% if feature_group_by_rollup_cube %}\n"
        "## ROLLUP {#rollup}\n"
        "{% endif %}\n"
        "## Other\n"
    )
    assert check_heading_parity(ru, en) == []


def test_md_link_parity_ignores_stripped_basenames():
    """§6.156: intentional strip must not re-block via md_link_parity."""
    ru = "See [t](table.md) and [c](create-resource-pool-classifier.md).\n"
    en = "See t and c.\n"
    assert check_md_link_parity(
        ru,
        en,
        source_lang="ru",
        target_lang="en",
        source_file="ydb/docs/ru/core/dev/system-views.md",
        ignore_basenames={"table.md", "create-resource-pool-classifier.md"},
    ) == []
