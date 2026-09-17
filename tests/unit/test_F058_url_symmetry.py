"""F-058: compare complete internal URLs after relative-path normalization."""

from ydbdoc_review.validation.href_parity import check_href_parity


def test_F058_equivalent_urls():
    ru = "[Настройки](./guide/../guide.md?mode=full#раздел-1)\n"
    en = (
        "[Settings](guide.md?mode=%66ull#%D1%80%D0%B0%D0%B7%D0%B4%D0%B5%D0%BB-1)\n"
    )

    assert check_href_parity(ru, en) == []


def test_F058_different_section():
    ru = "[Настройки](guide.md?mode=full#section-1)\n"
    en = "[Settings](other-guide.md?mode=full#section-1)\n"

    messages = check_href_parity(ru, en)

    assert messages
    assert "guide.md?mode=full#section-1" in messages[0]
    assert "other-guide.md?mode=full#section-1" in messages[0]
