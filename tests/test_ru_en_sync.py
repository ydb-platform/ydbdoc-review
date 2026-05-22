from ydbdoc_review.ru_en_sync import merge_section_h3_from_ru, section_missing_h3, split_h3_blocks


def test_section_missing_h3_detects_gap():
    ru = "## A\n\n### One\n\nru one\n\n### Two\n\nru two\n"
    en = "## A\n\n### One\n\nen one\n"
    assert section_missing_h3(ru, en)


def test_merge_section_adds_missing_h3():
    ru = "## A\n\n### One\n\nru one\n\n### Two\n\nru two\n"
    en = "## A\n\n### One\n\nen one\n"
    calls: list[str] = []

    def fake_translate(block: str) -> str:
        calls.append(block)
        if "ru two" in block:
            return "### Two\n\nen two translated"
        return block.replace("ru", "en")

    out = merge_section_h3_from_ru(ru, en, fake_translate)
    assert "en two translated" in out
    assert "en one" in out
    assert any("ru two" in c for c in calls)


def test_split_h3_blocks_keys():
    parts = split_h3_blocks("## X\n\nlead\n\n### Foo\n\nbody\n")
    keys = [k for k, _ in parts]
    assert "" in keys
    assert "foo" in keys
