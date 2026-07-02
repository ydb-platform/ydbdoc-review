"""Tests for autotitle href preservation on EN→RU translate."""

from ydbdoc_review.validation.autotitle_hrefs import restore_autotitle_hrefs


def test_restore_index_md_autotitle_hrefs():
    ru = "* [{#T}](../backup-and-recovery/index.md)\n"
    en_style = "* [{#T}](../backup-and-recovery.md)\n"
    assert restore_autotitle_hrefs(en_style, ru) == ru


def test_restore_skips_when_counts_differ():
    ru = "* [{#T}](./a.md)\n* [{#T}](./b.md)\n"
    en_style = "* [{#T}](./a.md)\n"
    assert restore_autotitle_hrefs(en_style, ru) == en_style
