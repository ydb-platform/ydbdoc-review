"""Tests for autotitle href preservation across locale translates."""

from ydbdoc_review.validation.autotitle_hrefs import restore_autotitle_hrefs


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


def test_restore_without_force_keeps_different_stems():
    ru = "[{#T}](query_execution/execution_process.md#sessions)\n"
    en = "[{#T}](query_execution/index.md#sessions)\n"
    assert restore_autotitle_hrefs(en, ru) == en
