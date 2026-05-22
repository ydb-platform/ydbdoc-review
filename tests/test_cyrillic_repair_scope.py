from ydbdoc_review.translate_postprocess import (
    en_contains_cyrillic,
    en_contains_cyrillic_prose,
)


def test_cyrillic_in_fence_does_not_flag_prose():
    en = "```yql\nSELECT 1 -- пример\n```\n\nEnglish paragraph.\n"
    assert en_contains_cyrillic(en)
    assert not en_contains_cyrillic_prose(en)


def test_cyrillic_in_prose_flags_prose():
    en = "Some **English** text with кириллица leak.\n"
    assert en_contains_cyrillic_prose(en)
