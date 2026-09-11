"""F-063: stable canonical explicit heading ids."""

from ydbdoc_review.validation.href_parity import check_heading_anchor_parity
from ydbdoc_review.validation.yfm_anchor import is_ascii_yfm_anchor


def test_F063_stable_id():
    ru = "# Старый заголовок {#Stable:ID}\n"
    en = "# New translated heading {#Stable:ID}\n"

    assert is_ascii_yfm_anchor("Stable:ID")
    assert check_heading_anchor_parity(ru, en) == []


def test_F063_invalid_or_swapped():
    assert not is_ascii_yfm_anchor("has spaces")
    assert not is_ascii_yfm_anchor("bad/section")

    ru = "# One {#first-id}\n# Two {#second-id}\n"
    swapped = "# Один {#second-id}\n# Два {#first-id}\n"
    assert check_heading_anchor_parity(ru, swapped)

    slug_substitution = "# Translated title\n"
    assert check_heading_anchor_parity(ru.split("\n", 1)[0], slug_substitution)
