"""F-083: RU wins for touched pairs while unrelated EN navigation survives."""

from __future__ import annotations

from ydbdoc_review.navigation.toc import merge_en_toc_yaml
from ydbdoc_review.pipeline.navigation_merge import extra_toc_hrefs_for_pair

EN_MAIN = (
    "items:\n"
    "- name: Existing EN\n  href: existing.md\n"
    "- name: Legacy EN\n  href: legacy.md\n"
)
RU_PR = (
    "items:\n"
    "- name: Изменённый раздел\n  href: existing.md\n"
    "- name: Новая страница\n  href: new-page.md\n"
)


def test_F083_bilingual_entries() -> None:
    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs={"existing.md", "new-page.md"},
        translate_name=lambda name: {
            "Изменённый раздел": "Changed section",
            "Новая страница": "New page",
        }.get(name, name),
    )
    assert "- name: Changed section" in merged
    assert "- name: New page" in merged
    assert "legacy.md" in merged
    assert "- name: Existing EN" not in merged


def test_F083_unchanged_source_toc() -> None:
    """An already placed RU page still supplies its minimal target TOC entry."""
    assert extra_toc_hrefs_for_pair(RU_PR, {"new-page.md"}) == {"new-page.md"}
    assert extra_toc_hrefs_for_pair(RU_PR, {"missing.md"}) == set()
