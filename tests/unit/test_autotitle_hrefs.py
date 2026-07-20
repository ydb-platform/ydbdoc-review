"""Tests for autotitle href preservation across locale translates."""

from ydbdoc_review.validation.autotitle_hrefs import (
    overlay_autotitle_fragment_hrefs,
    restore_autotitle_hrefs,
)


def test_restore_index_md_autotitle_hrefs():
    ru = "* [{#T}](../backup-and-recovery/index.md)\n"
    en_style = "* [{#T}](../backup-and-recovery.md)\n"
    assert restore_autotitle_hrefs(en_style, ru) == ru


def test_restore_skips_when_counts_differ():
    ru = "* [{#T}](./a.md)\n* [{#T}](./b.md)\n"
    en_style = "* [{#T}](./a.md)\n"
    assert restore_autotitle_hrefs(en_style, ru) == en_style


def test_restore_force_exact_ru_to_en_sessions_href():
    """#47100 / YFM010: LLM emitted stale query_execution/index.md#sessions."""
    ru = (
        "Более подробно сессии описаны в разделе "
        "[{#T}](query_execution/execution_process.md#sessions).\n"
    )
    en_bad = (
        "Sessions are described in more detail in the section "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
    fixed = restore_autotitle_hrefs(en_bad, ru, force_exact=True)
    assert "execution_process.md#sessions" in fixed
    assert "index.md#sessions" not in fixed


def test_restore_force_exact_repairs_bare_autotitle_after_strip():
    """#47108: strip_unreachable left bare ``{#T}`` instead of a link."""
    ru = (
        "Логические соединения с базой данных. Более подробно сессии описаны "
        "в разделе [{#T}](query_execution/execution_process.md#sessions).\n"
    )
    en_bare = (
        "Logical connections to the database. Sessions are described in more "
        "detail in the section {#T}.\n"
    )
    fixed = restore_autotitle_hrefs(en_bare, ru, force_exact=True)
    assert (
        "[{#T}](query_execution/execution_process.md#sessions)" in fixed
    )
    assert " section {#T}." not in fixed


def test_restore_without_force_keeps_different_stems():
    ru = "[{#T}](query_execution/execution_process.md#sessions)\n"
    en = "[{#T}](query_execution/index.md#sessions)\n"
    assert restore_autotitle_hrefs(en, ru) == en


def test_restore_force_exact_fragment_when_link_counts_differ():
    """#47104: after strip, counts differ — still fix unique #sessions href."""
    ru = (
        "See [{#T}](other.md#alpha) and "
        "[{#T}](query_execution/execution_process.md#sessions).\n"
    )
    en = (
        "See stripped and "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
    fixed = restore_autotitle_hrefs(en, ru, force_exact=True)
    assert "execution_process.md#sessions" in fixed
    assert "index.md#sessions" not in fixed


def test_overlay_autotitle_fragment_hrefs_prefers_main_sessions_target():
    """§6.128: merge-commit RU still has index.md#sessions; main moved it."""
    merge_ru = (
        "Сессии описаны в [{#T}](query_execution/index.md#sessions).\n"
    )
    main_ru = (
        "Сессии описаны в [{#T}](query_execution/execution_process.md#sessions).\n"
    )
    out = overlay_autotitle_fragment_hrefs(merge_ru, main_ru)
    assert "execution_process.md#sessions" in out
    assert "index.md#sessions" not in out


def test_overlay_then_force_exact_fixes_en_yfm010():
    merge_ru = (
        "See [{#T}](other.md#alpha) and "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
    main_ru = (
        "See [{#T}](other.md#alpha) and "
        "[{#T}](query_execution/execution_process.md#sessions).\n"
    )
    en_bad = (
        "See [{#T}](other.md#alpha) and "
        "[{#T}](query_execution/index.md#sessions).\n"
    )
    ru = overlay_autotitle_fragment_hrefs(merge_ru, main_ru)
    fixed = restore_autotitle_hrefs(en_bad, ru, force_exact=True)
    assert "execution_process.md#sessions" in fixed
    assert "index.md#sessions" not in fixed
